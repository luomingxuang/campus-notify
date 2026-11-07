"""在线站点导出：把抓取结果变成可公开分享的静态站。

产出（默认写到 <项目根>/4-交付物/在线站点/）：
- index.html  公网可看的面板（在线版，带 RSS 发现链接与订阅说明）
- feed.xml    RSS 2.0 订阅源，任何阅读器都能订阅

设计约定：
1. 站点是纯静态文件，不依赖后端，可直接丢到任何静态托管（GitHub Pages / 对象存储 / 本机发布）。
2. 所有链接指向学校官网原文，本工具不复制、不存储正文全文，只存摘要。
3. base_url 用于生成绝对地址；未配置时退化为相对路径，浏览器与多数阅读器仍可用。
"""
import html as html_lib
import logging
from datetime import datetime
from pathlib import Path

import config
from state import StateStore, format_pub_time, parse_deadline, parse_pub_dt, render_panel

logger = logging.getLogger("campus-notify.site")

# strftime 的 %a/%b 受系统 locale 影响（中文 Windows 会输出中文），RSS 必须用英文缩写
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

FEED_TITLE = "河南财政金融学院 · 校园通知"
FEED_DESC = "学校官网公告、教务处、学生处、计算机与人工智能学院通知的自动聚合，含 AI 摘要与截止提醒。"


def rfc822(dt: datetime) -> str:
    """RFC 822 时间格式（RSS pubDate 要求），固定东八区，不依赖系统 locale。"""
    return (
        f"{_WEEKDAYS[dt.weekday()]}, {dt.day:02d} {_MONTHS[dt.month - 1]} {dt.year} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} +0800"
    )


def _item_time(record: dict) -> datetime:
    """条目的时间：优先发布日期，退化为首次抓取时间，再退化为当前时间。"""
    for field in ("published_date", "first_seen_at"):
        dt = parse_pub_dt(record.get(field))
        if dt is not None:
            return dt
    return datetime.now()


def _item_html(record: dict) -> str:
    """把摘要、要做的事、截止时间拼成一段简洁的 HTML 描述。"""
    parts = []
    summary = (record.get("summary") or "").strip()
    if summary:
        parts.append(f"<p>{html_lib.escape(summary)}</p>")
    action = (record.get("action") or "").strip()
    if action:
        parts.append(f"<p><b>要做的事：</b>{html_lib.escape(action)}</p>")
    deadline = (record.get("deadline") or "").strip()
    if deadline:
        parts.append(f"<p><b>截止：</b>{html_lib.escape(deadline)}</p>")
    audience = (record.get("audience") or "").strip()
    if audience and audience != "不限":
        parts.append(f"<p>面向：{html_lib.escape(audience)}</p>")
    parts.append(
        f'<p><a href="{html_lib.escape(record["url"])}">阅读原文（{html_lib.escape(record["site_name"])}）</a></p>'
    )
    return "".join(parts)


def public_site_url(base_url: str = "", share_code: str = "") -> str:
    """站点首页对外地址（供同学打开）。未配置 base 时返回相对路径。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return "index.html"
    return f"{base}/?sharecode={share_code}" if share_code else f"{base}/"


def public_feed_url(base_url: str = "", share_code: str = "") -> str:
    """RSS 订阅源的对外地址。托管平台要求带 sharecode，否则静态资源返回 403。"""
    base = (base_url or "").rstrip("/")
    if not base:
        return "feed.xml"
    url = f"{base}/feed.xml"
    return f"{url}?sharecode={share_code}" if share_code else url


def build_rss(
    rows: list[dict], base_url: str = "", limit: int = 60, share_code: str = ""
) -> str:
    """生成 RSS 2.0 文档。rows 越新越靠前，超出 limit 的截断。"""
    ordered = sorted(rows, key=lambda r: _item_time(r).timestamp(), reverse=True)[:limit]
    build_dt = datetime.now()
    channel_link = public_site_url(base_url, share_code)
    self_link = public_feed_url(base_url, share_code)

    items = []
    for r in ordered:
        dt = _item_time(r)
        title = f"[{r['site_name']}] {r['title']}"
        desc = _item_html(r).replace("]]>", "]]&gt;")
        category = (r.get("category") or "").strip()
        cat_xml = f"\n    <category>{html_lib.escape(category)}</category>" if category else ""
        items.append(
            f"""  <item>
    <title>{html_lib.escape(title)}</title>
    <link>{html_lib.escape(r['url'])}</link>
    <guid isPermaLink="true">{html_lib.escape(r['url'])}</guid>
    <pubDate>{rfc822(dt)}</pubDate>{cat_xml}
    <description><![CDATA[{desc}]]></description>
  </item>"""
        )

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>{html_lib.escape(FEED_TITLE)}</title>
  <link>{html_lib.escape(channel_link)}</link>
  <description>{html_lib.escape(FEED_DESC)}</description>
  <language>zh-cn</language>
  <lastBuildDate>{rfc822(build_dt)}</lastBuildDate>
  <generator>campus-notify</generator>
  <atom:link href="{html_lib.escape(self_link)}" rel="self" type="application/rss+xml"/>
{chr(10).join(items) if items else "  "}
</channel>
</rss>
"""


def export_site(
    out_dir: Path | None = None,
    store: StateStore | None = None,
    base_url: str | None = None,
    share_code: str | None = None,
) -> dict:
    """导出静态站点，返回 {index, feed, count, site_url, feed_url}。"""
    out_dir = out_dir or config.SITE_DIR
    st = store if isinstance(store, StateStore) else StateStore().load()
    rows = st.articles()
    base = (config.SITE_PUBLIC_URL if base_url is None else base_url) or ""
    code = (config.SITE_SHARE_CODE if share_code is None else share_code) or ""

    out_dir.mkdir(parents=True, exist_ok=True)

    feed_url = public_feed_url(base, code)
    site_url = public_site_url(base, code)

    index_path = out_dir / "index.html"
    index_html = render_panel(rows, st.data.get("crawl_log", []), online=True, feed_href=feed_url)
    index_path.write_text(index_html, encoding="utf-8")

    feed_path = out_dir / "feed.xml"
    feed_path.write_text(build_rss(rows, base, share_code=code), encoding="utf-8")

    logger.info("在线站点已导出：%s（%d 条通知）", out_dir, len(rows))
    return {
        "index": index_path,
        "feed": feed_path,
        "count": len(rows),
        "site_url": site_url,
        "feed_url": feed_url,
    }


def describe_deadlines(rows: list[dict], days: int = 7) -> list[str]:
    """未来 N 天内截止的条目文案（站点首页/推送可复用）。"""
    out = []
    today = datetime.now().date()
    for r in rows:
        d = parse_deadline(r.get("deadline", ""))
        if d is None:
            continue
        left = (d - today).days
        if 0 <= left <= days:
            when, rel = format_pub_time(r.get("published_date"))
            out.append(f"{d}（还有 {left} 天）{r['title']}")
    return out
