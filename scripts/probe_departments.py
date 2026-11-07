"""探测各二级单位站点：能否复用现有解析逻辑（博达 CMS + v_news_content）。

背景：目前只接了 4 个站点。要推广到全校，必须先确认 20 个学院 + 各职能部门的站点
是不是同一套 CMS、有没有独立的「通知公告」栏目、现有 parser 能不能直接吃下来。

关键点：本脚本**直接复用 parser.parse_list_page / parse_article_page**，
而不是自己写一套正则。测出来的「可接入」才是真实可接入。

站点清单来源：
    https://www.hafu.edu.cn/jxyb.htm      教学院部 20 个
    https://www.hafu.edu.cn/zzjg/dqjg.htm 党群机构
    https://www.hafu.edu.cn/zzjg/xzbm.htm 行政部门
    https://www.hafu.edu.cn/zzjg/zsdw.htm 直属单位
抓取日期 2026-11-06；官网调整机构后需同步更新此表。

用法：
    python scripts/probe_departments.py             # 全部
    python scripts/probe_departments.py 学院         # 只看某一类
    python scripts/probe_departments.py --json      # 结果另存 JSON，便于生成配置
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: F401  触发 .env 加载
import parser

requests.packages.urllib3.disable_warnings()

# 探测时不做条数截断，否则看不出栏目真实规模
config.MAX_ARTICLES_PER_SITE = 999

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

UNITS: list[tuple[str, str, str]] = [
    ("学院", "财政税务学院", "http://czswxy.hafu.edu.cn/"),
    ("学院", "金融学院", "http://jrxy.hafu.edu.cn/"),
    ("学院", "会计学院", "http://kjxy.hafu.edu.cn/"),
    ("学院", "经济与贸易学院", "http://gjmyxy.hafu.edu.cn/"),
    ("学院", "工商管理学院", "http://gsglxy.hafu.edu.cn/"),
    ("学院", "计算机与人工智能学院", "http://jxxy.hafu.edu.cn/"),
    ("学院", "软件学院", "http://rjxy.hafu.edu.cn/"),
    ("学院", "工程经济学院", "http://gcjjxy.hafu.edu.cn/"),
    ("学院", "文化与传播学院", "http://whcbxy.hafu.edu.cn/"),
    ("学院", "法学院", "http://fxy.hafu.edu.cn/"),
    ("学院", "外国语学院", "http://wgyxy.hafu.edu.cn/"),
    ("学院", "统计与数学学院", "http://tjsxxy.hafu.edu.cn/"),
    ("学院", "教育科学学院", "http://jykxxy.hafu.edu.cn/"),
    ("学院", "旅游管理学院", "http://lyglxy.hafu.edu.cn/"),
    ("学院", "环境经济学院", "http://hjjjxy.hafu.edu.cn/"),
    ("学院", "体育与健康管理学院", "http://tyyjkglxy.hafu.edu.cn/"),
    ("学院", "艺术设计学院", "http://yssjxy.hafu.edu.cn/"),
    ("学院", "音乐学院", "http://yyxy.hafu.edu.cn/"),
    ("学院", "国际教育学院", "http://gjjyxy.hafu.edu.cn/"),
    ("学院", "马克思主义学院", "http://mkszyxy.hafu.edu.cn/"),
    ("部门", "教务处", "http://jwc.hafu.edu.cn/"),
    ("部门", "学生处", "http://xsc.hafu.edu.cn/"),
    ("部门", "团委", "http://tw.hafu.edu.cn/"),
    ("部门", "招生就业处", "http://zsjyc.hafu.edu.cn/"),
    ("部门", "科研处", "http://kyc.hafu.edu.cn/"),
    ("部门", "人事处", "http://rsc.hafu.edu.cn/"),
    ("部门", "财务处", "http://cwc.hafu.edu.cn/"),
    ("部门", "保卫处", "http://bwc.hafu.edu.cn/"),
    ("部门", "图书馆", "http://tsg.hafu.edu.cn/"),
    ("直属", "心理健康教育与咨询中心", "http://xlzxzx.hafu.edu.cn/"),
    ("直属", "后勤管理服务中心", "http://hqglc.hafu.edu.cn/"),
    ("直属", "创新创业中心", "http://cxcyzx.hafu.edu.cn/"),
    ("直属", "信息化办公室", "http://nimc.hafu.edu.cn/"),
]

# 栏目页文件名线索：越靠前越像「通知公告」
COLUMN_HINTS = ["tzgg", "ggtz", "tzgg1", "tzgg2", "tzgglist", "notice", "gonggao"]
# 次一级：新闻动态类，可作为兜底（内容仍有价值，只是偏新闻）
NEWS_HINTS = ["xwdt", "zhxw", "xyxw", "gzdt", "xwzx", "news", "dtxw", "jwxw"]
# 明显不是内容列表的栏目
SKIP_FILE = {"index.htm", "default.htm", "main.htm", "map.htm", "contact.htm"}

LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>\s*([^<]{0,30})')
JSP_RE = re.compile(r"(?:nry|content)\.jsp\?urltype=news\.NewsContentUrl")

SESSION = requests.Session()
SESSION.headers.update(UA)


def fetch(url: str, timeout: int = 15) -> str | None:
    try:
        r = SESSION.get(url, timeout=timeout, verify=False)
        if r.status_code >= 400:
            return None
        r.encoding = r.apparent_encoding or r.encoding
        return r.text
    except Exception:
        return None


def rank_candidates(home_html: str, base: str) -> list[str]:
    """从首页挑出候选栏目页，按「像不像通知公告」排序。"""
    strong: list[str] = []
    weak: list[str] = []
    for href, _text in LINK_RE.findall(home_html):
        if href.startswith(("javascript", "#", "mailto")) or "info/" in href:
            continue
        if not href.endswith(".htm"):
            continue
        low = href.lower().rsplit("/", 1)[-1]
        if low in SKIP_FILE:
            continue
        full = urljoin(base, href)
        if any(h in low for h in COLUMN_HINTS):
            if full not in strong:
                strong.append(full)
        elif any(h in low for h in NEWS_HINTS):
            if full not in weak:
                weak.append(full)
    return strong + weak


def try_list(base: str, list_url: str) -> tuple[list[dict], bool]:
    """用现有 parser 解析该列表页，返回（条目, 首篇正文是否可解析）。"""
    html = fetch(list_url)
    if html is None:
        return [], False
    site = {
        "id": "probe",
        "name": "probe",
        "list_url": list_url,
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": base,
    }
    items = parser.parse_list_page(site, html)
    if not items:
        return [], False
    detail = fetch(items[0]["url"])
    if detail is None:
        return items, False
    art = parser.parse_article_page(detail, items[0]["list_title"])
    return items, art is not None


def probe(kind: str, name: str, base: str) -> dict:
    row = {
        "类别": kind,
        "单位": name,
        "base_url": base,
        "状态": "不可达",
        "列表页": "",
        "条数": 0,
        "正文": "✗",
        "jsp漏抓": 0,
    }
    home = fetch(base)
    if home is None:
        return row
    row["状态"] = "可达"
    row["jsp漏抓"] = len(JSP_RE.findall(home))

    # 候选顺序：专用公告栏 → 新闻类栏目 → 首页本身
    for cand in (rank_candidates(home, base)[:4] + [base]):
        items, detail_ok = try_list(base, cand)
        if len(items) >= 3:
            row["列表页"] = cand
            row["条数"] = len(items)
            row["正文"] = "✓" if detail_ok else "✗"
            row["状态"] = "可接入" if detail_ok else "列表可读但正文失败"
            break
    if row["状态"] == "可达":
        row["状态"] = "无可用列表页"
    return row


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    want = args[0] if args else ""
    units = [u for u in UNITS if not want or u[0] == want]
    print(f"探测 {len(units)} 个站点（复用现有 parser）…\n")

    rows: list[dict] = []
    for i, (kind, name, base) in enumerate(units, 1):
        r = probe(kind, name, base)
        rows.append(r)
        flag = "OK  " if r["状态"] == "可接入" else "!!  "
        print(f"{flag}{i:2d}. [{kind}] {name:12s} {r['状态']:9s} 列表{r['条数']:3d} 条 正文{r['正文']}")
        if r["列表页"]:
            print(f"       {r['列表页']}   jsp 文章 {r['jsp漏抓']}")
        time.sleep(0.6)

    ok = [r for r in rows if r["状态"] == "可接入"]
    print("\n" + "=" * 64)
    print(f"合计 {len(rows)} 站：可接入 {len(ok)} 站，需单独处理 {len(rows) - len(ok)} 站")
    if len(rows) - len(ok):
        print("\n需单独处理：")
        for r in rows:
            if r["状态"] != "可接入":
                print(f"  - [{r['类别']}] {r['单位']}：{r['状态']}（列表 {r['条数']} 条，正文 {r['正文']}）")

    out = Path(__file__).resolve().parent.parent.parent.parent / "_临时" / "部门探测结果.json"
    if "--json" in sys.argv and out.parent.exists():
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
