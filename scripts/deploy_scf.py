"""一键部署到腾讯云函数 SCF。

做的事情（全自动，无需进控制台点击）：
    1. 校验凭证并读取账号 APPID
    2. 创建私有 COS 桶（状态快照存储，已存在则复用）
    3. 创建或更新云函数（代码包直接上传，含全部依赖）
    4. 写入环境变量（COS + DeepSeek + 抓取参数）
    5. 配置定时触发器（默认每 30 分钟）
    6. 立即触发一次做端到端验证

凭证读取顺序：
    TENCENTCLOUD_SECRET_ID / TENCENTCLOUD_SECRET_KEY 环境变量
    → 2-代码/campus-notify/.deploy.env
    → 2-代码/campus-notify/.env

用法：
    python scripts/deploy_scf.py --dry-run      # 只检查，不创建任何资源
    python scripts/deploy_scf.py                # 正式部署
    python scripts/deploy_scf.py --region ap-guangzhou
"""
import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
ZIP_PATH = REPO_ROOT / "4-交付物" / "campus-notify-scf.zip"
REPORT_PATH = REPO_ROOT / "4-交付物" / "部署结果.json"

DEFAULT_REGION = "ap-beijing"
DEFAULT_FUNCTION = "campus-notify"
DEFAULT_CRON = "0 */30 * * * * *"      # SCF 七段式：秒 分 时 日 月 周 年
DEFAULT_TRIGGER = "every-30min"
RUNTIME = "Python3.10"
HANDLER = "handler.main_handler"
TIMEOUT = 300
MEMORY = 256


# ---------------- 配置读取 ----------------

def load_env_file(path: Path) -> dict:
    data = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def resolve_settings() -> dict:
    env = load_env_file(PROJECT_ROOT / ".env")
    deploy = load_env_file(PROJECT_ROOT / ".deploy.env")

    def pick(*names, default=""):
        for n in names:
            for src in (os.environ, deploy, env):
                if src.get(n):
                    return src[n]
        return default

    secret_id = pick("TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID")
    secret_key = pick("TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        raise SystemExit(
            "缺少腾讯云密钥。请创建 2-代码/campus-notify/.deploy.env，写入：\n"
            "  TENCENT_SECRET_ID=你的SecretId\n"
            "  TENCENT_SECRET_KEY=你的SecretKey\n"
            "（SecretId/SecretKey 在腾讯云控制台「访问管理 → API 密钥管理」创建）"
        )

    return {
        "secret_id": secret_id,
        "secret_key": secret_key,
        "appid": pick("TENCENT_APPID"),
        "region": pick("SCF_REGION", default=DEFAULT_REGION),
        "function_name": pick("SCF_FUNCTION_NAME", default=DEFAULT_FUNCTION),
        "bucket": pick("COS_BUCKET"),   # 留空则部署时按 campus-notify-<appid> 自动生成
        "llm_base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
        "llm_api_key": env.get("LLM_API_KEY", ""),
        "llm_model": env.get("LLM_MODEL", "deepseek-chat"),
        "max_per_site": pick("MAX_ARTICLES_PER_SITE", default="6"),
        "max_age_days": pick("MAX_ARTICLE_AGE_DAYS", default="120"),
    }


# ---------------- 腾讯云客户端 ----------------

def make_credential(secret_id: str, secret_key: str):
    from tencentcloud.common.credential import Credential

    return Credential(secret_id, secret_key)


def fetch_appid(cred, region: str) -> str:
    from tencentcloud.sts.v20180813 import models, sts_client

    client = sts_client.StsClient(cred, region)
    resp = client.GetCallerIdentity(models.GetCallerIdentityRequest())
    return resp.AccountId


# ---------------- COS ----------------

def ensure_bucket(cfg: dict, appid: str, dry_run: bool) -> str:
    from cos_client import CosClient

    bucket = cfg["bucket"] or f"campus-notify-{appid}"
    if not bucket.endswith(f"-{appid}"):
        bucket = f"{bucket}-{appid}" if not bucket.rsplit("-", 1)[-1].isdigit() else bucket

    print(f"[2/6] COS 桶：{bucket}（地域 {cfg['region']}）")
    if dry_run:
        print("      跳过（dry-run）")
        return bucket

    client = CosClient(cfg["secret_id"], cfg["secret_key"], cfg["region"], bucket)
    try:
        if client.bucket_exists():
            print("      已存在，复用")
        else:
            created = client.create_bucket()
            print("      已创建（私有读写）" if created else "      已存在，复用")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(
            f"COS 桶操作失败：{exc}\n"
            "常见原因：① 密钥无 COS 权限 ② 账号未实名认证 ③ 桶名被他人占用"
        ) from exc
    return bucket


# ---------------- SCF ----------------

def function_exists(client, name: str) -> bool:
    from tencentcloud.scf.v20180416 import models

    req = models.ListFunctionsRequest()
    req.Filters = [{"Name": "FunctionName", "Values": [name]}]
    resp = client.ListFunctions(req)
    return any(f.FunctionName == name for f in (resp.Functions or []))


def build_env_vars(cfg: dict, bucket: str) -> dict:
    return {
        "STATE_BACKEND": "cos",
        "COS_SECRET_ID": cfg["secret_id"],
        "COS_SECRET_KEY": cfg["secret_key"],
        "COS_REGION": cfg["region"],
        "COS_BUCKET": bucket,
        "COS_KEY": "campus-notify/state.json",
        "LLM_BASE_URL": cfg["llm_base_url"],
        "LLM_API_KEY": cfg["llm_api_key"],
        "LLM_MODEL": cfg["llm_model"],
        "MAX_ARTICLES_PER_SITE": cfg["max_per_site"],
        "MAX_ARTICLE_AGE_DAYS": cfg["max_age_days"],
    }


def ensure_function(client, cfg: dict, bucket: str, dry_run: bool) -> None:
    from tencentcloud.scf.v20180416 import models

    name = cfg["function_name"]
    env_vars = build_env_vars(cfg, bucket)
    print(f"[3/6] 云函数：{name}（{RUNTIME}，{MEMORY}MB，超时 {TIMEOUT}s）")

    if dry_run:
        print("      跳过（dry-run）；环境变量将写入：", ", ".join(sorted(env_vars)))
        return

    if not ZIP_PATH.exists():
        raise SystemExit(f"部署包不存在：{ZIP_PATH}\n请先运行 python scripts/build_scf_package.py")

    zip_b64 = base64.b64encode(ZIP_PATH.read_bytes()).decode()
    exists = function_exists(client, name)

    if not exists:
        req = models.CreateFunctionRequest()
        req.FunctionName = name
        req.Runtime = RUNTIME
        req.Handler = HANDLER
        req.Code = {"ZipFile": zip_b64}
        req.Description = "校园通知抓取与摘要（自动生成）"
        req.MemorySize = MEMORY
        req.Timeout = TIMEOUT
        req.Environment = {"Variables": env_vars}
        try:
            client.CreateFunction(req)
            print("      已创建")
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"创建函数失败：{exc}") from exc
        return

    print("      已存在，更新代码与配置")
    update_code = models.UpdateFunctionCodeRequest()
    update_code.FunctionName = name
    update_code.Handler = HANDLER
    update_code.ZipFile = zip_b64
    client.UpdateFunctionCode(update_code)

    update_conf = models.UpdateFunctionConfigurationRequest()
    update_conf.FunctionName = name
    update_conf.MemorySize = MEMORY
    update_conf.Timeout = TIMEOUT
    update_conf.Environment = {"Variables": env_vars}
    client.UpdateFunctionConfiguration(update_conf)
    print("      代码与配置已更新")


def ensure_trigger(client, cfg: dict, dry_run: bool) -> None:
    from tencentcloud.scf.v20180416 import models

    name = cfg["function_name"]
    print(f"[4/6] 定时触发器：{DEFAULT_TRIGGER}（cron {DEFAULT_CRON}，每 30 分钟）")
    if dry_run:
        print("      跳过（dry-run）")
        return

    list_req = models.ListTriggersRequest()
    list_req.FunctionName = name
    existing = [t.TriggerName for t in (client.ListTriggers(list_req).Triggers or [])]
    if DEFAULT_TRIGGER in existing:
        print("      已存在，跳过")
        return

    req = models.CreateTriggerRequest()
    req.FunctionName = name
    req.TriggerName = DEFAULT_TRIGGER
    req.Type = "timer"
    req.TriggerDesc = DEFAULT_CRON
    req.Qualifier = "DEFAULT"
    req.Enable = "OPEN"
    client.CreateTrigger(req)
    print("      已创建")


def invoke_once(client, cfg: dict, dry_run: bool) -> dict | None:
    from tencentcloud.scf.v20180416 import models

    print("[6/6] 立即触发一次做端到端验证……")
    if dry_run:
        print("      跳过（dry-run）")
        return None

    req = models.InvokeRequest()
    req.FunctionName = cfg["function_name"]
    req.InvocationType = "RequestResponse"
    req.Qualifier = "DEFAULT"

    last_exc = None
    for attempt in range(6):
        try:
            resp = client.Invoke(req)
            raw = resp.Result.RetVal
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", "replace")
            return json.loads(raw) if raw.strip().startswith("{") else {"raw": raw}
        except Exception as exc:  # noqa: BLE001  函数刚创建时可能还没就绪
            last_exc = exc
            print(f"      第 {attempt + 1} 次调用失败，5 秒后重试：{str(exc)[:120]}")
            time.sleep(5)
    return {"ok": False, "error": f"调用失败：{last_exc}"}


# ---------------- 主流程 ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="一键部署校园通知抓取到腾讯云函数")
    ap.add_argument("--dry-run", action="store_true", help="只校验配置，不创建资源")
    ap.add_argument("--region", help="覆盖地域，默认 ap-beijing")
    ap.add_argument("--function", help="覆盖函数名，默认 campus-notify")
    ap.add_argument("--skip-invoke", action="store_true", help="部署后不立即触发")
    args = ap.parse_args()

    cfg = resolve_settings()
    if args.region:
        cfg["region"] = args.region
    if args.function:
        cfg["function_name"] = args.function

    cred = make_credential(cfg["secret_id"], cfg["secret_key"])

    print("[1/6] 校验凭证与账号")
    appid = cfg["appid"]
    if not appid:
        try:
            appid = fetch_appid(cred, cfg["region"])
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"无法获取账号 APPID：{exc}\n"
                "请在 .deploy.env 里补充 TENCENT_APPID=你的APPID（控制台「账号信息」可查）"
            ) from exc
    print(f"      APPID：{appid} | 地域：{cfg['region']}")
    if not cfg["llm_api_key"]:
        print("      ⚠️ 未从 .env 读到 LLM_API_KEY，云函数将降级为「仅标题+链接」")

    bucket = ensure_bucket(cfg, appid, args.dry_run)

    from tencentcloud.scf.v20180416 import scf_client

    client = scf_client.ScfClient(cred, cfg["region"])
    ensure_function(client, cfg, bucket, args.dry_run)
    ensure_trigger(client, cfg, args.dry_run)

    print("[5/6] 等待函数就绪")
    if args.dry_run:
        print("      跳过（dry-run）")
        result = None
    else:
        time.sleep(8)
        result = None if args.skip_invoke else invoke_once(client, cfg, args.dry_run)

    report = {
        "deployed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "region": cfg["region"],
        "function_name": cfg["function_name"],
        "bucket": bucket,
        "trigger_cron": DEFAULT_CRON,
        "invoke_result": result,
        "dry_run": args.dry_run,
    }
    if not args.dry_run:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 52)
    if args.dry_run:
        print("配置检查通过。去掉 --dry-run 即可正式部署。")
        return 0

    print("部署完成")
    print(f"  函数：{cfg['function_name']}（{cfg['region']}）")
    print(f"  状态桶：{bucket}/campus-notify/state.json")
    print(f"  定时：每 30 分钟一次")
    if result and result.get("ok"):
        stats = result.get("stats", {})
        print(f"  首次运行成功：新增 {stats.get('new', 0)} / 未变化 {stats.get('unchanged', 0)} "
              f"/ 失败 {stats.get('failed', 0)}")
    elif result:
        print(f"  首次运行未通过：{str(result)[:300]}")
    print(f"  详情见：{REPORT_PATH}")
    print("=" * 52)
    return 0


if __name__ == "__main__":
    sys.exit(main())
