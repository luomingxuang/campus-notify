"""解析器：从列表页提取文章链接，从详情页提取标题/日期/正文。

四个站点均为博达 CMS 结构：
- 列表页含指向 /info/栏目/文章ID.htm 的链接
- 详情页正文位于 <div class="v_news_content">
"""
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import config
from fetcher import fetch

logger = logging.getLogger("campus-notify.parser")

DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2})?")

# 标题清洗：去掉博达 CMS 自动拼接的「-站名」后缀，如 "-河南财政金融学院--教务处"
TITLE_SUFFIX_RE = re.compile(r"[-—－]\s*河南财政金融学院.*$")


def clean_title(raw: str) -> str:
    return TITLE_SUFFIX_RE.sub("", raw).strip()


def parse_list_page(site: dict, html: str) -> list[dict]:
    """从列表页提取文章条目：url + 列表页标题文本。"""
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(site["article_pattern"])
    seen: set[str] = set()
    items: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not pattern.search(href):
            continue
        url = urljoin(site["base_url"], href)
        if url in seen:
            continue
        text = a.get_text(strip=True)
        if not text or len(text) < 6:  # 过滤掉图片链接等无意义锚文本
            continue
        seen.add(url)
        items.append({"url": url, "list_title": clean_title(text)})
    return items[: config.MAX_ARTICLES_PER_SITE]


def _extract_date(soup: BeautifulSoup, html: str) -> str:
    for tag in soup.find_all(class_=re.compile(r"(author|news_time|time|postion)")):
        m = DATE_RE.search(tag.get_text())
        if m:
            return m.group(0)
    m = DATE_RE.search(soup.get_text())
    return m.group(0) if m else ""


def parse_article_page(
    html: str, fallback_title: str, allow_empty_content: bool = False
) -> dict | None:
    """从详情页提取标题/日期/正文；正文过短视为失败。

    allow_empty_content=True 用于「通知本体是附件、正文区为空」的站点（如招生就业处），
    这类文章只有标题也有价值（至少知道有这件事），此时返回 content=""，由摘要层标注说明。
    """
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    for tag in soup.find_all(["h1", "h2"], class_=re.compile(r"(article|news).*title")):
        text = tag.get_text(strip=True)
        if len(text) >= 6:
            title = text
            break
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
    if not title:
        title = fallback_title
    title = clean_title(title)

    content_div = soup.find("div", class_="v_news_content")
    if content_div is None:
        logger.warning("详情页缺少 v_news_content，标题=%s", title[:30])
        return None
    content = content_div.get_text("\n", strip=True)
    # 压缩空白
    content = re.sub(r"\n{3,}", "\n\n", content)
    if len(content) < 30:
        if not allow_empty_content:
            return None
        content = ""  # 正文区为空（内容在附件里），保留标题信息
    return {
        "title": title,
        "published_date": _extract_date(soup, html),
        "content": content[:20000],  # 截断超长正文，控制 LLM 成本
    }
