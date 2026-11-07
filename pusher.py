"""微信推送：把通知汇总直接推到手机。

两个通道都不需要自己注册公众号、不需要买服务器：
- serverchan（Server酱）：微信扫码登录即得 SendKey，免费 5 条/天 → https://sct.ftqq.com
- pushplus（推送加）：免费 200 条/天，需手机号实名 → https://www.pushplus.plus

未配置推送通道时静默跳过，不影响抓取与面板生成。

推送策略（PUSH_MODE）：
- daily（默认）：每天到点推一次汇总，一天最多一条；电脑当天晚开机则开机后补推
- instant：只要本轮有新通知就推一条
"""
import logging
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

import requests

import config
from state import format_pub_time, parse_deadline

logger = logging.getLogger("campus-notify.pusher")

SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"
SERVERCHAN_API_V3 = "https://{uid}.push.ft07.com/send/{key}.send"
PUSHPLUS_API = "https://www.pushplus.plus/send"

# 本机 Windows 通知用的 PowerShell 脚本（不依赖任何第三方模块）
TOAST_SCRIPT = Path(__file__).resolve().parent / "scripts" / "show_toast.ps1"

# 单条推送正文里每条通知摘要的最大字数（避免消息过长被折叠）
SUMMARY_CLIP = 120
ACTION_CLIP = 80


def _serverchan_endpoint(sendkey: str) -> str:
    """新版 SendKey 以 sctp 开头时，走带 uid 子域名的 v3 接口。"""
    if sendkey.startswith("sctp"):
        parts = sendkey[4:].split("t")
        if parts and parts[0].isdigit():
            return SERVERCHAN_API_V3.format(uid=parts[0], key=sendkey)
    return SERVERCHAN_API.format(key=sendkey)


def _send_serverchan(title: str, content: str) -> tuple[bool, str]:
    resp = requests.post(
        _serverchan_endpoint(config.SERVERCHAN_SENDKEY),
        data={"title": title[:32], "desp": content},
        timeout=15,
    )
    try:
        payload = resp.json()
    except ValueError:
        return False, f"Server酱返回非 JSON（HTTP {resp.status_code}）"
    if payload.get("code") == 0:
        return True, "ok"
    return False, f"Server酱错误：{payload.get('message') or payload}"


def _send_pushplus(title: str, content: str) -> tuple[bool, str]:
    resp = requests.post(
        PUSHPLUS_API,
        json={
            "token": config.PUSHPLUS_TOKEN,
            "title": title[:100],
            "content": content,
            "template": "markdown",
        },
        timeout=15,
    )
    try:
        payload = resp.json()
    except ValueError:
        return False, f"PushPlus 返回非 JSON（HTTP {resp.status_code}）"
    if payload.get("code") == 200:
        return True, "ok"
    return False, f"PushPlus 错误：{payload.get('msg') or payload}"


def _strip_md(text: str) -> str:
    """把 Markdown 摘要压成适合系统通知的纯文本（去标题井号、加粗、链接语法）。"""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or set(line) <= set("-—_ "):
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = line.replace("**", "").replace("`", "")
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _send_windows(title: str, message: str) -> tuple[bool, str]:
    """弹一条本机 Windows 系统通知，点一下打开「看最新通知.html」。不装任何第三方模块。"""
    if not TOAST_SCRIPT.exists():
        return False, f"缺少弹窗脚本 {TOAST_SCRIPT}"
    if sys.platform != "win32":
        return False, "windows 通道只能在 Windows 本机使用"

    panel = config.PROJECT_ROOT / "看最新通知.html"
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(TOAST_SCRIPT),
        "-Title",
        title,
        "-Message",
        message,
    ]
    if panel.exists():
        cmd += ["-OpenPath", str(panel)]

    kwargs = {"creationflags": 0x08000000}  # CREATE_NO_WINDOW：pythonw 下不闪黑框
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=25, **kwargs
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"弹窗调用失败：{exc}"

    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out.startswith("ok"):
        return True, "ok"
    err = out or (proc.stderr or "").strip()[:200]
    return False, f"弹窗失败：{err}"


def send(title: str, content: str, rows: list[dict] | None = None) -> tuple[bool, str]:
    """按配置的通道逐个发送，返回 (是否至少一个成功, 说明)。异常不外抛，只记日志。

    rows 传入待推送记录时，系统通知会另生成一份更短的文案；不传则从 Markdown 正文里截。
    """
    channels = config.enabled_channels()
    if not channels:
        return False, "未配置推送通道"

    toast_title, toast_body = "", ""
    oks: list[str] = []
    fails: list[str] = []
    for name in channels:
        try:
            if name == "serverchan":
                ok, msg = _send_serverchan(title, content)
            elif name == "pushplus":
                ok, msg = _send_pushplus(title, content)
            else:
                if not toast_body:
                    if rows:
                        toast_title, toast_body = build_toast(rows)
                    else:
                        toast_title, toast_body = title, _strip_md(content)
                ok, msg = _send_windows(toast_title, toast_body)
        except requests.RequestException as exc:
            logger.error("%s 推送请求失败：%s", name, exc)
            ok, msg = False, f"网络错误：{exc}"
        except Exception as exc:  # noqa: BLE001 单个通道出错不能拖垮其它通道
            logger.error("%s 通道异常：%s", name, exc)
            ok, msg = False, f"异常：{exc}"

        if ok:
            oks.append(name)
        else:
            fails.append(f"{name}（{msg}）")

    if not oks:
        return False, "全部通道失败：" + "；".join(fails)

    detail = "成功：" + "、".join(oks)
    if fails:
        detail += "；失败：" + "；".join(fails)
    return True, detail


def _deadline_note(deadline: str) -> str:
    """把截止日期渲染成一句人类能看懂的话；已过期或无法解析则返回空。"""
    if not deadline:
        return ""
    d = parse_deadline(deadline)
    if d is None:
        return f"截止 {deadline}"
    days = (d - date.today()).days
    if days < 0:
        return ""
    if days == 0:
        return f"**⚠️ 今天截止** {deadline}"
    if days <= 3:
        return f"**⚠️ {days} 天后截止** {deadline}"
    return f"截止 {deadline}"


def _order(rows: list[dict]) -> list[dict]:
    """按发布日期从新到旧排列；无发布时间的按抓取时间倒序排后面。"""

    def key(r: dict):
        from state import parse_pub_dt

        dt = parse_pub_dt(r.get("published_date"))
        if dt is not None:
            return (0, -dt.timestamp())
        fs = parse_pub_dt(r.get("first_seen_at"))
        return (1, -(fs.timestamp() if fs else 0.0))

    return sorted(rows, key=key)


def _clamp(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text[:limit] + "…" if len(text) > limit else text


def _urgent_lines(rows: list[dict], max_items: int = 3) -> list[str]:
    """3 天内截止的通知单独置顶摘要（防按时间排序后被挤出列表而漏掉）。"""
    today = date.today()
    urgent = []
    for r in rows:
        d = parse_deadline(r.get("deadline", ""))
        if d is None:
            continue
        days = (d - today).days
        if 0 <= days <= 3:
            urgent.append((d.toordinal(), days, r))
    if not urgent:
        return []
    urgent.sort(key=lambda x: x[0])
    lines = ["**⏰ 快截止（别漏）**"]
    for _, days, r in urgent[:max_items]:
        when = "今天" if days == 0 else f"{days} 天后"
        lines.append(f"- {when}截止 · {r['title'][:26]}")
    if len(urgent) > max_items:
        lines.append(f"- 另有 {len(urgent) - max_items} 条 3 天内截止")
    lines.append("")
    return lines


def build_digest(rows: list[dict], total: int | None = None) -> tuple[str, str]:
    """把待推送通知渲染成 (标题, Markdown 正文)；条目按发布时间从新到旧。"""
    ordered = _order(rows)
    shown = ordered[: config.PUSH_MAX_ITEMS]
    total = total if total is not None else len(rows)

    title = f"校园通知 · {total} 条更新" if total != 1 else "校园通知 · 1 条更新"
    newest = format_pub_time(ordered[0].get("published_date"))[0] if ordered else ""
    lines = [f"## 🏫 校园通知 · {total} 条", ""]
    if newest:
        lines.append(f"> 按发布时间排序，最新一条 {newest}")
        lines.append("")

    lines += _urgent_lines(ordered)

    for idx, r in enumerate(shown, 1):
        cat = r.get("category") or "未分类"
        site = r.get("site_name", "")
        time_text, rel = format_pub_time(r.get("published_date"))
        meta = [f"📅 {time_text}" + (f"（{rel}）" if rel else ""), f"`{cat}`"]
        if site:
            meta.append(site)
        audience = (r.get("audience") or "").strip()
        if audience and audience != "不限":
            meta.append(f"面向{audience}")

        lines.append(f"**{idx}. {r['title']}**")
        lines.append(" · ".join(meta))

        # 微信里越短越好扫：优先用一句话结论，没有才退回整段摘要
        lede = _clamp(r.get("headline"), SUMMARY_CLIP) or _clamp(r.get("summary"), SUMMARY_CLIP)
        if lede:
            lines.append(lede)

        action = _clamp(r.get("action"), ACTION_CLIP)
        if action:
            lines.append(f"👉 **要做**：{action}")

        note = _deadline_note(r.get("deadline", ""))
        if note:
            lines.append(f"⏰ {note}")
        lines.append(f"[查看原文]({r['url']})")
        lines.append("")

    rest = total - len(shown)
    if rest > 0:
        lines.append(f"---\n另有 **{rest} 条**较早的通知，打开电脑上的「看最新通知.html」看完整列表。")

    lines.append("")
    lines.append(f"_抓取时间 {datetime.now():%Y-%m-%d %H:%M} · 校园通知提醒_")
    return title, "\n".join(lines)


def build_toast(rows: list[dict], total: int | None = None) -> tuple[str, str]:
    """系统通知版文案：只放得下 4 行，标题写条数，正文放最新几条（快截止的带 ⏰ 前缀）。"""
    ordered = _order(rows)
    total = total if total is not None else len(rows)
    title = f"校园通知 · {total} 条更新" if total != 1 else "校园通知 · 1 条更新"

    urgent_key = None
    for r in ordered:  # 3 天内截止的项：出现在列表里就加 ⏰，挤不进来则单占一行
        d = parse_deadline(r.get("deadline", ""))
        if d is None:
            continue
        if 0 <= (d - date.today()).days <= 3:
            urgent_key = r.get("url")
            break

    shown = ordered[:4]
    has_urgent = any(r.get("url") == urgent_key for r in shown)
    lines: list[str] = []
    if urgent_key and not has_urgent:
        for r in ordered:
            if r.get("url") == urgent_key:
                days = (parse_deadline(r.get("deadline", "")) - date.today()).days
                when = "今天" if days == 0 else f"{days} 天后"
                lines.append(f"⏰ {when}截止 {_clamp(r['title'], 16)}")
                break
        shown = ordered[:3]

    for r in shown:
        mark = "⏰ " if r.get("url") == urgent_key else "· "
        lines.append(f"{mark}{_clamp(r['title'], 24)}")

    rest = total - len(shown)
    if rest > 0:
        lines.append(f"…另有 {rest} 条，点开看全部")
    return title, "\n".join(lines)


def _published_within(record: dict, days: int) -> bool:
    """发布日期是否在最近 days 天内（日期解析不出来时保守保留）。"""
    raw = (record.get("published_date") or "")[:10]
    try:
        published = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return True
    return (date.today() - published).days <= days


def maybe_push(st, new_count: int = 0) -> dict:
    """按配置判断是否该推送，需要推就推。返回结果摘要（不抛异常）。"""
    if not config.validate_push_config():
        return {"pushed": False, "reason": "未配置推送通道"}

    used = st.push_count_today()
    if used >= config.PUSH_DAILY_LIMIT:
        return {"pushed": False, "reason": f"今日推送已达上限 {config.PUSH_DAILY_LIMIT} 条"}

    unpushed = [a for a in st.articles() if not a.get("pushed")]
    if not unpushed:
        return {"pushed": False, "reason": "没有待推送的通知"}

    # 超出时效窗口的老通知静默归档，不打扰（首次配置时尤其重要，否则一次推几十条）
    pending = [a for a in unpushed if _published_within(a, config.PUSH_MAX_AGE_DAYS)]
    keep = {st.key_of(a["site_id"], a["url"]) for a in pending}
    archived = 0
    for article in unpushed:
        if st.key_of(article["site_id"], article["url"]) not in keep:
            article["pushed"] = True
            archived += 1
    if archived:
        logger.info(
            "归档 %d 条超过 %d 天的老通知（不推送）", archived, config.PUSH_MAX_AGE_DAYS
        )
    if not pending:
        reason = f"归档 {archived} 条老通知，无可推送内容" if archived else "没有待推送的通知"
        return {"pushed": False, "reason": reason}

    if config.PUSH_MODE == "instant":
        if new_count <= 0:
            return {"pushed": False, "reason": "本轮无新增"}
    else:
        if used > 0:
            return {"pushed": False, "reason": "今日已推送过"}
        if datetime.now().hour < config.PUSH_DAILY_HOUR:
            return {"pushed": False, "reason": f"未到推送时间（{config.PUSH_DAILY_HOUR}:00 之后）"}

    title, content = build_digest(pending)
    ok, msg = send(title, content, rows=pending)
    if not ok:
        logger.error("推送失败：%s", msg)
        return {"pushed": False, "reason": msg}

    for article in pending:
        article["pushed"] = True
    st.record_push()
    logger.info("推送成功：%s（覆盖 %d 条通知，通道 %s）", title, len(pending), msg)
    return {"pushed": True, "reason": msg, "count": len(pending), "title": title}


def send_test() -> tuple[bool, str]:
    """手动测试推送通道是否配通（不标记任何文章为已推送）。"""
    title = "校园通知提醒 · 推送测试"
    content = "\n".join(
        [
            "## ✅ 推送通道已打通",
            "",
            "配置成功。之后每轮抓取完成后会自动把新通知推到这里。",
            "",
            f"- 通道：`{config.PUSH_CHANNEL}`",
            f"- 模式：`{config.PUSH_MODE}`（daily = 每天一条汇总）",
            f"- 测试时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        ]
    )
    return send(title, content)
