"""把云端（COS）的状态快照拉到本地并导出 Markdown，方便随时查看。

用法：python scripts/pull_state.py
产物：4-交付物/云端通知摘要.md

配置来源：
    1. 4-交付物/部署结果.json（部署脚本产出，含桶名与地域）
    2. .deploy.env / .env 里的 COS_* 与 TENCENT_* 值
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PROJECT_ROOT.parent.parent
REPORT_PATH = REPO_ROOT / "4-交付物" / "部署结果.json"
OUTPUT_PATH = REPO_ROOT / "4-交付物" / "云端通知摘要.md"

sys.path.insert(0, str(PROJECT_ROOT))


def load_settings() -> dict:
    """读取部署凭证：环境变量 > .deploy.env > .env。"""
    def read(path: Path) -> dict:
        out = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
        return out

    env = read(PROJECT_ROOT / ".env")
    deploy = read(PROJECT_ROOT / ".deploy.env")

    def pick(*names, default=""):
        for n in names:
            for src in (os.environ, deploy, env):
                if src.get(n):
                    return src[n]
        return default

    secret_id = pick("TENCENTCLOUD_SECRET_ID", "TENCENT_SECRET_ID")
    secret_key = pick("TENCENTCLOUD_SECRET_KEY", "TENCENT_SECRET_KEY")
    if not secret_id or not secret_key:
        raise SystemExit("缺少腾讯云密钥，请先创建 2-代码/campus-notify/.deploy.env")
    return {"secret_id": secret_id, "secret_key": secret_key,
            "region": pick("SCF_REGION", default="ap-beijing")}


def main() -> int:
    try:
        cfg = load_settings()
    except SystemExit as exc:
        print(exc)
        return 1

    if not REPORT_PATH.exists():
        print(f"未找到部署记录 {REPORT_PATH}，请先运行 python scripts/deploy_scf.py")
        return 1

    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    bucket = report.get("bucket")
    region = report.get("region", cfg["region"])
    if not bucket:
        print("部署记录里没有桶名，请检查 部署结果.json")
        return 1

    from cos_client import CosClient
    from state import StateStore

    client = CosClient(cfg["secret_id"], cfg["secret_key"], region, bucket)
    print(f"读取 COS：{bucket}/campus-notify/state.json（{region}）")
    raw = client.get_object("campus-notify/state.json")
    if raw is None:
        print("云端状态对象还不存在 —— 说明云函数还没有成功跑过一轮")
        return 1

    data = json.loads(raw.decode("utf-8"))
    articles = data.get("articles", {})
    with_summary = sum(1 for r in articles.values() if r.get("summary"))

    class _MemBackend:
        def load(self):
            return raw.decode("utf-8")

        def save(self, payload):
            pass

    store = StateStore(_MemBackend()).load()
    text = store.export_md(OUTPUT_PATH)

    print(f"云端共 {len(articles)} 条通知，其中 {with_summary} 条有 AI 摘要")
    last_crawl = data.get("crawl_log", [])
    if last_crawl:
        print(f"最近一次抓取：{last_crawl[-1].get('at', '未知')}")
    print(f"已导出：{OUTPUT_PATH}（{len(text)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
