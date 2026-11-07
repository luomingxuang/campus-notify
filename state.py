"""状态层：JSON 快照存储，去重键 = 站点 + URL + 正文哈希。

两种持久化后端（云函数无持久磁盘，必须能存到对象存储）：
- local：本地文件（开发/手动运行）
- cos：腾讯云 COS 对象（云函数运行）

结构：
{
  "version": 1,
  "articles": { "<site_id>|<url>": {...,} },
  "crawl_log": [ {...} ]
}
"""
import hashlib
import html as html_lib
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path

import config
import fav_local
import src_filter

logger = logging.getLogger("campus-notify.state")

STATE_VERSION = 1
MAX_CRAWL_LOG = 200


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 持久化后端 ----------------

class LocalBackend:
    """本地文件后端。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> str | None:
        if self.path.exists():
            return self.path.read_text(encoding="utf-8")
        return None

    def save(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self.path)


class CosBackend:
    """腾讯云 COS 对象后端（云函数环境使用）。"""

    def __init__(self, secret_id: str, secret_key: str, region: str, bucket: str, key: str):
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.region = region
        self.bucket = bucket
        self.key = key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from cos_client import CosClient  # 纯标准库实现，无二进制依赖

            self._client = CosClient(
                secret_id=self.secret_id,
                secret_key=self.secret_key,
                region=self.region,
                bucket=self.bucket,
            )
        return self._client

    def load(self) -> str | None:
        raw = self._get_client().get_object(self.key)
        if raw is None:
            logger.info("COS 状态对象不存在，按首次运行处理")
            return None
        return raw.decode("utf-8")

    def save(self, payload: str) -> None:
        self._get_client().put_object(
            self.key, payload.encode("utf-8"), content_type="application/json"
        )


def build_backend():
    backend = os.environ.get("STATE_BACKEND", "local").lower()
    if backend == "cos":
        required = {
            "COS_SECRET_ID": os.environ.get("COS_SECRET_ID", ""),
            "COS_SECRET_KEY": os.environ.get("COS_SECRET_KEY", ""),
            "COS_REGION": os.environ.get("COS_REGION", ""),
            "COS_BUCKET": os.environ.get("COS_BUCKET", ""),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise RuntimeError(f"STATE_BACKEND=cos 但缺少环境变量：{', '.join(missing)}")
        return CosBackend(
            secret_id=required["COS_SECRET_ID"],
            secret_key=required["COS_SECRET_KEY"],
            region=required["COS_REGION"],
            bucket=required["COS_BUCKET"],
            key=os.environ.get("COS_KEY", "campus-notify/state.json"),
        )
    return LocalBackend(config.DATA_DIR / "state.json")


# ---------------- 状态仓库 ----------------

class StateStore:
    def __init__(self, backend=None):
        self.backend = backend or build_backend()
        self.data = {"version": STATE_VERSION, "articles": {}, "crawl_log": []}

    def load(self) -> "StateStore":
        raw = self.backend.load()
        if raw:
            try:
                self.data = json.loads(raw)
                self.data.setdefault("articles", {})
                self.data.setdefault("crawl_log", [])
                self.data.setdefault("push_history", {})
                # 向后兼容：老记录缺新字段时补默认值，避免渲染处 KeyError
                for record in self.data["articles"].values():
                    record.setdefault("action", "")
                    record.setdefault("audience", "")
                    record.setdefault("summary", "")
                    record.setdefault("category", "")
                    record.setdefault("deadline", "")
                    record.setdefault("published_date", "")
            except json.JSONDecodeError:
                logger.error("状态文件损坏，按空状态启动（历史去重将失效一轮）")
        return self

    def save(self) -> None:
        if len(self.data["crawl_log"]) > MAX_CRAWL_LOG:
            self.data["crawl_log"] = self.data["crawl_log"][-MAX_CRAWL_LOG:]
        self.backend.save(json.dumps(self.data, ensure_ascii=False, indent=1))

    @staticmethod
    def key_of(site_id: str, url: str) -> str:
        return f"{site_id}|{url}"

    def upsert(self, article: dict) -> tuple[dict, str]:
        """写入文章，返回 (记录, 状态 new/updated)。unchanged 时返回 (记录, 'unchanged')。"""
        key = self.key_of(article["site_id"], article["url"])
        existing = self.data["articles"].get(key)
        if existing is None:
            record = {
                "site_id": article["site_id"],
                "site_name": article["site_name"],
                "url": article["url"],
                "title": article["title"],
                "published_date": article.get("published_date", ""),
                "content_hash": article["content_hash"],
                "summary": "",
                "action": "",
                "audience": "",
                "category": "",
                "deadline": "",
                "pushed": False,
                "first_seen_at": now_str(),
                "updated_at": now_str(),
            }
            self.data["articles"][key] = record
            return record, "new"
        if existing["content_hash"] == article["content_hash"]:
            return existing, "unchanged"
        existing.update(
            {
                "title": article["title"],
                "published_date": article.get("published_date", ""),
                "content_hash": article["content_hash"],
                "updated_at": now_str(),
            }
        )
        return existing, "updated"

    def find(self, site_id: str, url: str) -> dict | None:
        return self.data["articles"].get(self.key_of(site_id, url))

    def set_summary(self, record: dict, summary: dict) -> None:
        record["summary"] = summary.get("summary", "")
        record["headline"] = summary.get("headline", "")
        record["points"] = list(summary.get("points") or [])
        record["content_empty"] = bool(summary.get("content_empty"))
        record["action"] = summary.get("action", "")
        record["audience"] = summary.get("audience", "")
        record["category"] = summary.get("category", "")
        record["deadline"] = summary.get("deadline", "")
        record["updated_at"] = now_str()

    def log_crawl(self, site_id: str, ok: bool, detail: str = "") -> None:
        self.data["crawl_log"].append(
            {"site_id": site_id, "ok": ok, "detail": detail, "at": now_str()}
        )

    # ---- 推送计数（每日额度保护，同时保证 daily 模式一天只推一条）----

    def push_count_today(self) -> int:
        return int(self.data.setdefault("push_history", {}).get(date.today().isoformat(), 0))

    def record_push(self) -> None:
        history = self.data.setdefault("push_history", {})
        key = date.today().isoformat()
        history[key] = history.get(key, 0) + 1
        for old in sorted(history)[:-30]:  # 只保留最近 30 天
            history.pop(old, None)

    def consecutive_failures(self, site_id: str, n: int = 3) -> bool:
        rows = [r for r in self.data["crawl_log"] if r["site_id"] == site_id][-n:]
        return len(rows) == n and all(not r["ok"] for r in rows)

    def articles(self) -> list[dict]:
        return list(self.data["articles"].values())

    def export_md(self, out_path: Path | None = None) -> str:
        """导出审阅用 Markdown（本地运行时写文件，云函数返回字符串）。"""
        rows = sorted(self.articles(), key=lambda r: r["first_seen_at"], reverse=True)[:50]
        lines = ["# 校园通知抓取结果", ""]
        for r in rows:
            lines.append(f"## [{r['site_name']}] {r['title']}")
            lines.append(f"- 原文: {r['url']}")
            lines.append(
                f"- 发布日期: {r['published_date'] or '未识别'} | 抓取时间: {r['first_seen_at']}"
            )
            if r["category"] or r["deadline"]:
                lines.append(f"- 类别: {r['category'] or '未分类'} | 截止: {r['deadline'] or '无'}")
            if r["summary"]:
                lines.append(f"- AI摘要: {r['summary']}")
            lines.append("")
        text = "\n".join(lines)
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
        return text

    def export_html(self, out_path: Path | None = None) -> str:
        """导出本地可视化面板（单文件 HTML，双击即看，无外部依赖）。"""
        rows = self.articles()
        text = render_panel(rows, self.data.get("crawl_log", []))
        if out_path:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
        return text


# ---------------- 本地面板渲染 ----------------

CATEGORY_STYLE = {
    "教务": ("#2563eb", "#eff6ff"),
    "通知": ("#7c3aed", "#f5f3ff"),
    "活动": ("#0891b2", "#ecfeff"),
    "财务": ("#d97706", "#fffbeb"),
    "其他": ("#64748b", "#f8fafc"),
}


def parse_deadline(text: str):
    """把 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' 解析成 date；解析不了返回 None。"""
    if not text:
        return None
    raw = text.strip().replace("/", "-")
    for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:width], fmt).date()
        except ValueError:
            continue
    return None


def parse_pub_dt(raw: str) -> datetime | None:
    """解析发布日期（可能只有日期，也可能带时分）。解析不了返回 None。"""
    raw = (raw or "").strip().replace("/", "-")
    if not raw:
        return None
    for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:width], fmt)
        except ValueError:
            continue
    return None


def format_pub_time(raw: str, now: datetime | None = None) -> tuple[str, str]:
    """返回 (显示时间, 相对时间)。例：('11-05 09:12', '昨天')。"""
    dt = parse_pub_dt(raw)
    if dt is None:
        return "时间未识别", ""
    now = now or datetime.now()
    days = (now.date() - dt.date()).days
    if days == 0:
        rel = "今天"
    elif days == 1:
        rel = "昨天"
    elif days == 2:
        rel = "前天"
    elif 3 <= days <= 6:
        rel = f"{days} 天前"
    elif days < 0:
        rel = ""
    else:
        rel = ""
    has_clock = bool(dt.hour or dt.minute)
    text = dt.strftime("%m-%d %H:%M") if has_clock else dt.strftime("%m-%d")
    return text, rel


def _deadline_badge(deadline: str) -> tuple[str, str]:
    """返回 (文案, 徽章样式类)。"""
    d = parse_deadline(deadline)
    if d is None:
        return "", "badge-none"
    days = (d - date.today()).days
    if days < 0:
        return f"已截止 {deadline}", "badge-over"
    if days == 0:
        return f"今天截止 {deadline}", "badge-urgent"
    if days <= 3:
        return f"{days} 天后截止 {deadline}", "badge-urgent"
    if days <= 7:
        return f"{days} 天后截止 {deadline}", "badge-soon"
    return f"截止 {deadline}", "badge-far"


def render_panel(
    rows: list[dict],
    crawl_log: list[dict] | None = None,
    online: bool = False,
    feed_href: str = "feed.xml",
) -> str:
    """渲染单文件面板。online=True 时追加 RSS 发现链接与「怎么订阅」说明，供公网站点使用。"""
    crawl_log = crawl_log or []
    today = date.today()

    def sort_key(r: dict):
        """排序：发布日期从新到旧（无发布时间的按抓取时间倒序排后面）。"""
        dt = parse_pub_dt(r.get("published_date"))
        if dt is not None:
            return (0, -dt.timestamp())
        fs = parse_pub_dt(r.get("first_seen_at"))
        return (1, -(fs.timestamp() if fs else 0.0))

    ordered = sorted(rows, key=sort_key)

    soon = sum(
        1 for r in rows
        if (d := parse_deadline(r.get("deadline", ""))) is not None and 0 <= (d - today).days <= 7
    )
    sites = sorted({r["site_name"] for r in rows})
    # 站点数以「配置里在抓的站点」为准，与「关注来源」面板里的勾选项数量一致；
    # 用 rows 里的去重站名会随本轮有无新文章而跳动，读起来像是站点被删了。
    site_count = len(config.SITES) or len(sites)
    categories = sorted({r["category"] for r in rows if r.get("category")})

    cards = []
    now_dt = datetime.now()
    for r in ordered:
        color, bg = CATEGORY_STYLE.get(r.get("category") or "其他", CATEGORY_STYLE["其他"])
        badge_text, badge_cls = _deadline_badge(r.get("deadline", ""))
        cat = r.get("category") or "未分类"
        summary = r.get("summary") or "（本条暂无 AI 摘要，可能是未配置模型或生成失败）"
        badge_html = f'<span class="badge {badge_cls}">{html_lib.escape(badge_text)}</span>' if badge_text else ""

        time_text, rel = format_pub_time(r.get("published_date"), now_dt)
        when_html = (
            f'<span class="when" title="发布日期">📅 {html_lib.escape(time_text)}'
            f'{(" · " + rel) if rel else ""}</span>'
        )

        action = (r.get("action") or "").strip()
        action_html = (
            f'<div class="action"><b>要做的事</b>{html_lib.escape(action)}</div>'
            if action else ""
        )

        # ---- 摘要：一句话结论 + 要点清单 ----
        headline = (r.get("headline") or "").strip()
        points = [p for p in (r.get("points") or []) if str(p).strip()]
        if headline or points:
            lede_html = f'<div class="lede">{html_lib.escape(headline)}</div>' if headline else ""
            points_html = (
                '<ul class="points">'
                + "".join(f"<li>{html_lib.escape(str(p))}</li>" for p in points)
                + "</ul>"
                if points else ""
            )
            summary_html = lede_html + points_html
        else:
            # 旧数据（升级前抓的）没有 headline/points，回退到整段摘要
            summary_html = f'<p class="summary">{html_lib.escape(summary)}</p>'

        # 正文为空（内容在附件/图片里）单独提示，不污染摘要正文
        empty_tip_html = (
            '<div class="attach-tip">📎 原文正文是图片或附件，摘要只能依据标题判断，细节请点开原文看。</div>'
            if r.get("content_empty") else ""
        )

        audience = (r.get("audience") or "").strip()
        meta_bits = [f"面向 {html_lib.escape(audience)}"] if audience and audience != "不限" else []
        meta_bits.append(f"抓取 {html_lib.escape((r.get('first_seen_at') or '-')[5:16])}")

        if online:
            fav_btn = (
                f'<button class="fav" data-url="{html_lib.escape(r["url"])}"'
                f' data-title="{html_lib.escape(r["title"])}"'
                f' data-site="{html_lib.escape(r["site_name"])}"'
                f' data-deadline="{html_lib.escape(r.get("deadline") or "")}"'
                f' data-pubdate="{html_lib.escape(r.get("published_date") or "")}"'
                f' aria-pressed="false">☆ 收藏</button>'
            )
            meta_html = f'<div class="meta"><span>{" · ".join(meta_bits)}</span>{fav_btn}</div>'
        else:
            meta_html = f'<div class="meta">{" · ".join(meta_bits)}</div>'

        pub_dt = parse_pub_dt(r.get("published_date"))
        pub_ts = int(pub_dt.timestamp()) if pub_dt else 0
        dl = parse_deadline(r.get("deadline", ""))
        dl_key = dl.toordinal() if dl is not None and (dl - today).days >= 0 else 99999999

        cards.append(
            f"""<article class="card" data-site="{html_lib.escape(r['site_name'])}"
     data-site-id="{html_lib.escape(r.get('site_id') or '')}"
     data-cat="{html_lib.escape(cat)}"
     data-pub="{pub_ts}"
     data-dl="{dl_key}"
     data-isfav="0"
     data-href="{html_lib.escape(r['url'])}"
     data-search="{html_lib.escape((r['title'] + ' ' + summary + ' ' + action).lower())}">
  <div class="card-top">
    <span class="cat" style="color:{color};background:{bg}">{html_lib.escape(cat)}</span>
    <span class="site">{html_lib.escape(r['site_name'])}</span>
    {when_html}
    {badge_html}
  </div>
  <h2><a href="{html_lib.escape(r['url'])}" target="_blank" rel="noopener">{html_lib.escape(r['title'])}</a><span class="go" aria-hidden="true">↗</span></h2>
  {summary_html}
  {empty_tip_html}
  {action_html}
  {meta_html}
</article>"""
        )

    cat_buttons = "".join(
        f'<button class="chip" data-cat="{html_lib.escape(c)}">{html_lib.escape(c)}</button>'
        for c in categories
    )
    site_options = "".join(
        f'<option value="{html_lib.escape(s)}">{html_lib.escape(s)}</option>' for s in sites
    )

    last_crawl = crawl_log[-1]["at"] if crawl_log else "-"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 在线版（公网站点）额外内容：RSS 自动发现 + 订阅说明 ----
    if online:
        feed_esc = html_lib.escape(feed_href)
        extra_head = (
            f'\n<link rel="alternate" type="application/rss+xml" '
            f'title="校园通知 · RSS" href="{feed_esc}">'
            f'\n<meta name="description" content="河南财政金融学院校园官网通知自动聚合，含 AI 摘要、要做的事与截止提醒">'
            f'\n<meta name="theme-color" content="#1e3a8a">'
        )
        extra_style = """
  .sub-box { max-width: 860px; margin: 10px auto 0; padding: 0 20px; font-size: 12.5px; color: #475569; }
  .sub-box h3 { font-size: 12.5px; margin: 0 0 6px; color: #334155; font-weight: 600; }
  .sub-box ol { margin: 0; padding-left: 20px; }
  .sub-box li { margin-bottom: 3px; }
  .sub-box code { background: #eef2ff; color: #4338ca; padding: 1px 6px; border-radius: 4px; font-size: 11.5px; }
  .sub-note { color: #94a3b8; font-size: 11.5px; margin: 8px 0 0; }
"""
        subscribe_html = f"""
<section class="sub-box">
  <h3>用起来更顺手的四个办法</h3>
  <ol>
    <li>手机浏览器打开本页 → 分享菜单里「添加到主屏幕」，以后点图标就能看，和有 App 一样。</li>
    <li>用 RSS 阅读器订阅 <code>{feed_esc}</code>，有更新会自动提醒。</li>
    <li>看到要办的事，点卡片右下角「☆ 收藏」攒成一份清单，再用「我的收藏」筛出来看。收藏保存在本机浏览器，不用注册登录；换设备或清缓存会丢。</li>
    <li>摘要只是帮你快速判断，细节要核实就<b>点卡片任意位置或标题</b>，直接打开学校官网原文。</li>
  </ol>
  <p class="sub-note">本页内容聚合自学校各官网公开页面，仅供同学参考，一切以官网原文与学校正式通知为准。</p>
</section>
"""
    else:
        extra_head = ""
        extra_style = ""
        subscribe_html = ""

    # ---- 收藏与关注来源（仅在线版）：纯本地存储，不登录、不依赖后端 ----
    if online:
        extra_style += fav_local.STYLE
        local_fav_html = fav_local.BODY
        src_html = src_filter.bundle(config.SITES, config.DEFAULT_SITE_IDS)
        site_select_hidden = " hidden"
        src_btn_hidden = ""
        fav_btn_hidden = ""
    else:
        local_fav_html = ""
        src_html = ""
        site_select_hidden = ""
        src_btn_hidden = " hidden"
        fav_btn_hidden = " hidden"

    template = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>校园通知提醒</title>__EXTRA_HEAD__
<style>
  * { box-sizing: border-box; }
  /* hidden 属性必须压过组件自己的 display —— 弹层/提示条都靠 display:flex 布局，
     若不加这条，作者样式的 display 会覆盖浏览器默认的 [hidden]{display:none}，
     结果 hidden 形同摆设：来源面板一打开就关不掉。用 !important 一劳永逸。 */
  [hidden] { display: none !important; }
  body {
    margin: 0; padding: 0 0 60px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f1f5f9; color: #0f172a; line-height: 1.6;
  }
  header {
    background: linear-gradient(135deg, #1e3a8a, #4f46e5);
    color: #fff; padding: 28px 20px 24px;
  }
  .wrap { max-width: 860px; margin: 0 auto; }
  header h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: .5px; }
  header .sub { font-size: 12.5px; opacity: .8; }
  .stats { display: flex; gap: 10px; margin-top: 18px; flex-wrap: wrap; }
  .stat {
    background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.22);
    border-radius: 10px; padding: 10px 16px; min-width: 96px;
  }
  .stat b { display: block; font-size: 21px; line-height: 1.2; }
  .stat span { font-size: 11.5px; opacity: .82; }
  .toolbar {
    max-width: 860px; margin: 18px auto 0; padding: 0 20px;
    display: flex; gap: 10px; flex-wrap: wrap; align-items: center;
  }
  input[type=search], select {
    font: inherit; font-size: 14px; padding: 9px 13px; border-radius: 9px;
    border: 1px solid #cbd5e1; background: #fff; color: #0f172a; outline: none;
  }
  input[type=search] { flex: 1; min-width: 180px; }
  input[type=search]:focus, select:focus { border-color: #6366f1; box-shadow: 0 0 0 3px rgba(99,102,241,.15); }
  .chips { max-width: 860px; margin: 12px auto 0; padding: 0 20px; display: flex; gap: 8px; flex-wrap: wrap; }
  .chip {
    font: inherit; font-size: 13px; cursor: pointer; padding: 5px 14px; border-radius: 999px;
    border: 1px solid #cbd5e1; background: #fff; color: #475569; transition: .15s;
  }
  .chip:hover { border-color: #818cf8; color: #4338ca; }
  .chip.active { background: #4f46e5; border-color: #4f46e5; color: #fff; }
  main { max-width: 860px; margin: 18px auto 0; padding: 0 20px; }
  .card {
    background: #fff; border: 1px solid #e2e8f0; border-radius: 13px;
    padding: 16px 18px; margin-bottom: 12px; box-shadow: 0 1px 2px rgba(15,23,42,.04);
    transition: .15s;
  }
  .card:hover { border-color: #c7d2fe; box-shadow: 0 4px 14px rgba(79,70,229,.1); transform: translateY(-1px); }
  .card-top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
  .cat { font-size: 11.5px; font-weight: 600; padding: 2px 9px; border-radius: 6px; }
  .site { font-size: 11.5px; color: #94a3b8; }
  .when { font-size: 11.5px; font-weight: 600; color: #4338ca; background: #eef2ff; padding: 2px 9px; border-radius: 6px; }
  .badge { font-size: 11.5px; font-weight: 600; padding: 2px 9px; border-radius: 6px; margin-left: auto; }
  .badge-urgent { color: #b91c1c; background: #fef2f2; }
  .badge-soon   { color: #c2410c; background: #fff7ed; }
  .badge-far    { color: #047857; background: #ecfdf5; }
  .badge-over   { color: #94a3b8; background: #f1f5f9; font-weight: 500; }
  .card h2 { margin: 0 0 6px; font-size: 16px; line-height: 1.45; font-weight: 600; }
  .card h2 a { color: #1e293b; text-decoration: none; }
  .card h2 a:hover { color: #4f46e5; text-decoration: underline; }
  .summary { margin: 0 0 8px; font-size: 13.5px; color: #475569; }
  /* 摘要改成「一句话结论 + 要点」后更好扫读：结论深色加粗，要点用轻量列表 */
  .lede { margin: 0 0 6px; font-size: 14px; color: #1e293b; font-weight: 500; line-height: 1.55; }
  .points { margin: 0 0 8px; padding-left: 17px; list-style: none; }
  .points li {
    position: relative; font-size: 13px; color: #475569;
    line-height: 1.65; margin-bottom: 2px;
  }
  .points li::before {
    content: ""; position: absolute; left: -12px; top: 9px;
    width: 4px; height: 4px; border-radius: 50%; background: #a5b4fc;
  }
  .attach-tip {
    margin: 0 0 8px; font-size: 12px; color: #92400e;
    background: #fefce8; border: 1px dashed #fcd34d; border-radius: 7px; padding: 5px 9px;
  }
  /* 整卡可点：右上角箭头给出「点了会跳走」的暗示，hover 时抬起并变蓝 */
  .card { cursor: pointer; }
  .card h2 { position: relative; padding-right: 22px; }
  .go {
    position: absolute; right: 0; top: 1px; font-size: 14px; color: #cbd5e1;
    transition: .15s;
  }
  .card:hover .go { color: #4f46e5; transform: translate(2px, -2px); }
  .card:active { transform: translateY(0); }
  .card:focus-within { border-color: #a5b4fc; box-shadow: 0 0 0 3px rgba(79,70,229,.14); }
  .tips {
    max-width: 860px; margin: 12px auto 0; padding: 0 20px;
    font-size: 12.5px; color: #475569;
  }
  .tips-inner {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    background: #eef2ff; border: 1px solid #c7d2fe; border-radius: 9px; padding: 8px 12px;
  }
  .tips-inner b { color: #3730a3; }
  .tips-inner button {
    margin-left: auto; font: inherit; font-size: 11.5px; cursor: pointer;
    border: 1px solid #a5b4fc; background: #fff; color: #4338ca;
    border-radius: 6px; padding: 3px 9px;
  }
  .action {
    margin: 0 0 8px; font-size: 13px; color: #92400e; background: #fffbeb;
    border-left: 3px solid #f59e0b; padding: 6px 10px; border-radius: 0 6px 6px 0;
  }
  .action b { margin-right: 6px; font-size: 11.5px; color: #b45309; letter-spacing: .3px; }
  .meta { font-size: 11.5px; color: #94a3b8; }
  .empty { text-align: center; color: #94a3b8; padding: 50px 0; font-size: 14px; }
  footer { max-width: 860px; margin: 26px auto 0; padding: 0 20px; font-size: 11.5px; color: #94a3b8; }
  mark { background: #fef08a; padding: 0 1px; }__EXTRA_STYLE__
</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>校园通知提醒</h1>
    <div class="sub">河南财政金融学院 · __SITE_COUNT__ 个来源站点自动抓取 + AI 摘要</div>
    <div class="stats">
      <div class="stat"><b>__TOTAL__</b><span>条通知</span></div>
      <div class="stat"><b>__SOON__</b><span>7 天内截止</span></div>
      <div class="stat"><b>__SITES__</b><span>个来源站点</span></div>
    </div>
  </div>
</header>

<div class="toolbar">
  <input type="search" id="q" placeholder="搜索标题、摘要、要做的事…">
  <select id="site"__SITE_SELECT_HIDDEN__>
    <option value="">全部站点</option>
    __SITE_OPTIONS__
  </select>
  <button class="chip" id="srcBtn"__SRC_BTN_HIDDEN__>来源</button>
  <select id="sort">
    <option value="pub">最新发布</option>
    <option value="dl">最近截止</option>
  </select>
  <button class="chip" id="favFilter"__FAV_BTN_HIDDEN__>☆ 我的收藏（0）</button>
</div>
<div class="tips" id="tips" hidden>
  <div class="tips-inner">
    <span>💡 <b>点卡片任意位置</b>就能打开官网原文（标题、正文都能点）；<b>☆ 收藏</b>只是标记，不会跳走。</span>
    <button id="tipsClose">知道了</button>
  </div>
</div>
<div class="chips">
  <button class="chip active" data-cat="">全部类别</button>
  __CAT_BUTTONS__
</div>

<main id="list">
__CARDS__
<div class="empty" id="empty" style="display:none">没有匹配的通知</div>
</main>

__SUBSCRIBE__
<footer>
  数据生成于 __GENERATED__ · 最近抓取 __LAST_CRAWL__ · 本页每次抓取后自动更新，刷新浏览器即可看到最新结果
</footer>

<script>
(function () {
  var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
  var q = document.getElementById('q');
  var siteSel = document.getElementById('site');
  var sortSel = document.getElementById('sort');
  var list = document.getElementById('list');
  var empty = document.getElementById('empty');
  var activeCat = '';
  var favOnly = false;

  function apply() {
    var kw = (q.value || '').trim().toLowerCase();
    var shown = 0;
    cards.forEach(function (c) {
      var ok = (!kw || c.dataset.search.indexOf(kw) !== -1)
            && siteOk(c)
            && (!activeCat || c.dataset.cat === activeCat)
            && (!favOnly || c.dataset.isfav === '1');
      c.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    if (!shown && favOnly) {
      empty.textContent = '这里还没有收藏的通知：点卡片右下角的「☆ 收藏」，把要做的事攒在这里。';
    } else if (!shown && window.__srcFilter) {
      empty.textContent = '当前关注的来源里没有通知。点上面的「来源」按钮，可以加上你的学院或其他单位。';
    } else {
      empty.textContent = shown ? '' : '没有匹配的通知';
    }
    empty.style.display = shown ? 'none' : '';
  }

  // 来源过滤：在线版走「关注来源」面板（localStorage），本地版退回站点下拉
  function siteOk(card) {
    if (window.__srcFilter) { return window.__srcFilter.matches(card.dataset.siteId); }
    return !siteSel || !siteSel.value || card.dataset.site === siteSel.value;
  }

  function resort() {
    var mode = sortSel.value;
    cards.sort(function (a, b) {
      if (mode === 'dl') {
        var da = parseInt(a.dataset.dl, 10) || 99999999;
        var db = parseInt(b.dataset.dl, 10) || 99999999;
        if (da !== db) return da - db;
      }
      return (parseInt(b.dataset.pub, 10) || 0) - (parseInt(a.dataset.pub, 10) || 0);
    });
    cards.forEach(function (c) { list.insertBefore(c, empty); });
  }

  q.addEventListener('input', apply);
  siteSel.addEventListener('change', apply);
  sortSel.addEventListener('change', resort);

  // 只取类别按钮：工具栏里的「我的收藏」同样是 .chip，但语义不同，不能参与类别切换
  var chips = Array.prototype.slice.call(document.querySelectorAll('.chips .chip'));
  chips.forEach(function (btn) {
    btn.addEventListener('click', function () {
      chips.forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
      activeCat = btn.dataset.cat;
      apply();
    });
  });

  window.__panelSetFavOnly = function (on) {
    favOnly = !!on;
    apply();
  };

  // 供「关注来源」面板在勾选变化后触发重筛
  window.__panelReapply = apply;

  // ---- 整卡可点：点卡片任意位置都打开官网原文 ----
  // 用事件委托而不是给每张卡绑，因为排序/筛选会移动 DOM 节点。
  list.addEventListener('click', function (e) {
    // 卡片里的链接、收藏按钮自己处理，别抢它们的点击
    if (e.target.closest('a, button, input, select, label')) { return; }
    // 中键/右键/带修饰键交给浏览器默认行为
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) { return; }
    // 正在选文字时别跳走，否则想复制摘要就被打断了
    var sel = window.getSelection ? String(window.getSelection()) : '';
    if (sel.length > 0) { return; }
    var card = e.target.closest('.card');
    if (!card) { return; }
    var href = card.getAttribute('data-href');
    if (href) { window.open(href, '_blank', 'noopener'); }
  });

  // ---- 首次提示：告诉读者怎么去原文，看过一次就不再打扰 ----
  (function initTips() {
    var box = document.getElementById('tips');
    if (!box) { return; }
    var KEY = 'campusNotify.tips.v1';
    try {
      if (localStorage.getItem(KEY) === 'off') { return; }
    } catch (err) { /* 无痕模式照常显示 */ }
    box.hidden = false;
    var closeBtn = document.getElementById('tipsClose');
    if (closeBtn) {
      closeBtn.addEventListener('click', function () {
        box.hidden = true;
        try { localStorage.setItem(KEY, 'off'); } catch (err) { /* 忽略 */ }
      });
    }
  })();
})();
</script>__LOCAL_FAV__
__SRC_FILTER__
</body>
</html>"""

    return (
        template.replace("__TOTAL__", str(len(rows)))
        .replace("__SOON__", str(soon))
        .replace("__SITES__", str(site_count))
        .replace("__SITE_COUNT__", str(site_count))
        .replace("__SITE_OPTIONS__", site_options)
        .replace("__CAT_BUTTONS__", cat_buttons)
        .replace("__CARDS__", "\n".join(cards) if cards else '<div class="empty">还没有抓到通知，等待第一轮抓取完成</div>')
        .replace("__EXTRA_HEAD__", extra_head)
        .replace("__EXTRA_STYLE__", extra_style)
        .replace("__LOCAL_FAV__", local_fav_html)
        .replace("__SRC_FILTER__", src_html)
        .replace("__SITE_SELECT_HIDDEN__", site_select_hidden)
        .replace("__SRC_BTN_HIDDEN__", src_btn_hidden)
        .replace("__FAV_BTN_HIDDEN__", fav_btn_hidden)
        .replace("__SUBSCRIBE__", subscribe_html)
        .replace("__GENERATED__", generated)
        .replace("__LAST_CRAWL__", last_crawl)
    )
