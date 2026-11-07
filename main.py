"""campus-notify 主流程（单轮抓取任务）：

列表页抓取 → 详情页解析 → 时效过滤 → 去重判定 → 状态入库 → 摘要 → 导出审阅文件

本地用法：
    python main.py            # 正常跑一轮（每站最多 10 篇）
    python main.py --limit 2  # 冒烟：每站只取 2 篇
    python main.py --backfill # 只给历史缺摘要的文章补生成

云函数入口见 handler.py
"""
import argparse
import logging
import sys
from datetime import date, datetime, timedelta

import config
import site_export
from fetcher import fetch
from parser import parse_article_page, parse_list_page
from pusher import maybe_push, send_test
from state import StateStore, content_hash, parse_pub_dt
from summarizer import summarize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("campus-notify.main")

EXPORT_PATH = config.DATA_DIR / "outputs" / "最新通知摘要.md"
PANEL_PATH = config.DATA_DIR / "outputs" / "通知面板.html"
# 项目根目录的快捷查看入口（双击即开，每次抓取后自动覆盖）
QUICK_PANEL_PATH = config.PROJECT_ROOT / "看最新通知.html"


def is_too_old(published_date: str) -> bool:
    """发布日期早于 MAX_ARTICLE_AGE_DAYS 天的视为旧闻，跳过。日期无法解析时不拦截。"""
    try:
        d = datetime.strptime(published_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return False
    return d < date.today() - timedelta(days=config.MAX_ARTICLE_AGE_DAYS)


def run_cycle(limit: int | None = None, store: StateStore | None = None) -> dict:
    """执行一轮抓取，返回统计信息（本地与云函数共用）。"""
    st = (store or StateStore()).load()
    llm_enabled = config.validate_llm_config()
    max_per_site = limit if limit is not None else config.MAX_ARTICLES_PER_SITE
    stats = {"new": 0, "updated": 0, "unchanged": 0, "skipped_old": 0, "failed": 0}

    for site in config.SITES:
        html = fetch(site["list_url"])
        if html is None:
            st.log_crawl(site["id"], ok=False, detail="列表页抓取失败")
            logger.error("[%s] 列表页抓取失败，跳过", site["id"])
            stats["failed"] += 1
            continue

        items = parse_list_page(site, html)
        logger.info("[%s] 列表页解析出 %d 篇文章", site["id"], len(items))
        site_new = 0
        for item in items[:max_per_site]:
            article_html = fetch(item["url"])
            if article_html is None:
                stats["failed"] += 1
                continue
            parsed = parse_article_page(
                article_html,
                item["list_title"],
                allow_empty_content=site.get("allow_empty_content", False),
            )
            if parsed is None:
                stats["failed"] += 1
                continue
            if is_too_old(parsed["published_date"]):
                stats["skipped_old"] += 1
                continue

            article = {
                "site_id": site["id"],
                "site_name": site["name"],
                "url": item["url"],
                "title": parsed["title"],
                "published_date": parsed["published_date"],
                "content_hash": content_hash(parsed["content"]),
                "content": parsed["content"],
            }
            record, status = st.upsert(article)
            if status == "unchanged":
                stats["unchanged"] += 1
                continue

            summary = summarize(article, llm_enabled)
            st.set_summary(record, summary)
            stats[status] += 1
            if status == "new":
                site_new += 1
                logger.info(
                    "[%s] 新通知：%s | 类别=%s | 截止=%s",
                    site["id"], parsed["title"][:26],
                    summary["category"] or "-", summary["deadline"] or "无",
                )
        st.log_crawl(site["id"], ok=True, detail=f"新入库 {site_new} 篇")
        logger.info("[%s] 完成，本轮新入库 %d 篇", site["id"], site_new)

    stats["alerts"] = warn_consecutive_failures(st)

    # 微信推送：未配置通道时静默跳过；推送失败不影响本轮抓取结果
    try:
        stats["push"] = maybe_push(st, new_count=stats["new"])
        if stats["push"].get("pushed"):
            logger.info("已推送微信：%s", stats["push"].get("title", ""))
        else:
            logger.info("本轮不推送：%s", stats["push"].get("reason", ""))
    except Exception as exc:  # noqa: BLE001 推送是附加能力，绝不能拖垮抓取
        logger.error("推送环节异常（不影响抓取）：%s", exc)
        stats["push"] = {"pushed": False, "reason": str(exc)}

    st.save()
    logger.info(
        "本轮结束：新增 %d / 更新 %d / 未变化 %d / 旧闻跳过 %d / 失败 %d",
        stats["new"], stats["updated"], stats["unchanged"], stats["skipped_old"], stats["failed"],
    )
    return stats


def backfill_summaries(
    llm_enabled: bool, limit: int = 20, store: StateStore | None = None
) -> int:
    """对已入库但缺摘要的文章补生成（重新抓正文 → 摘要），返回处理数量。"""
    if not llm_enabled:
        logger.warning("LLM 不可用，跳过补摘要")
        return 0
    st = (store or StateStore()).load()
    pending = [r for r in st.articles() if not r.get("summary")][:limit]
    done = 0
    for record in pending:
        html = fetch(record["url"])
        if html is None:
            logger.warning("补摘要抓取失败: %s", record["url"])
            continue
        parsed = parse_article_page(html, record["title"])
        if parsed is None:
            continue
        summary = summarize({"title": parsed["title"], "content": parsed["content"]}, llm_enabled)
        if not summary.get("summary"):
            logger.warning("补摘要失败（降级）: %s", record["title"][:24])
            continue
        st.set_summary(record, summary)
        done += 1
        logger.info(
            "摘要完成 [%s] %s | 类别=%s | 截止=%s",
            record["site_name"], parsed["title"][:28],
            summary["category"], summary["deadline"] or "无",
        )
    st.save()
    logger.info("补摘要完成：成功 %d / 待补 %d", done, len(pending))
    return done


def resummarize_all(
    llm_enabled: bool, limit: int = 400, store: StateStore | None = None
) -> int:
    """重抓正文并重新生成全部摘要（换了摘要口径后用语），返回处理数量。"""
    if not llm_enabled:
        logger.warning("LLM 不可用，跳过重生成摘要")
        return 0
    st = (store or StateStore()).load()
    rows = sorted(
        st.articles(),
        key=lambda r: r.get("published_date") or "",
        reverse=True,
    )[:limit]
    # 站点级开关：正文为空但内容在附件里的站（如招生就业处）要放行，
    # 否则重跑摘要时这类文章会被 parse 判为失败而永远漏掉。
    allow_empty_by_site = {
        s["id"]: s.get("allow_empty_content", False) for s in config.SITES
    }
    done = 0
    for record in rows:
        html = fetch(record["url"])
        if html is None:
            logger.warning("重生成抓取失败: %s", record["url"])
            continue
        parsed = parse_article_page(
            html,
            record["title"],
            allow_empty_content=allow_empty_by_site.get(record.get("site_id"), False),
        )
        if parsed is None:
            logger.warning("重生成解析失败（正文或容器异常）: %s", record["title"][:24])
            continue
        # 顺手用更精确的发布日期（可能带时分）覆盖旧值
        if parsed["published_date"] and parsed["published_date"] != record.get("published_date"):
            record["published_date"] = parsed["published_date"]
        summary = summarize({"title": parsed["title"], "content": parsed["content"]}, llm_enabled)
        if not summary.get("summary"):
            logger.warning("重生成失败（降级）: %s", record["title"][:24])
            continue
        st.set_summary(record, summary)
        done += 1
        logger.info(
            "已重写 [%s] %s | 类别=%s | 要做=%s | 截止=%s",
            record["site_name"], parsed["title"][:26],
            summary["category"], (summary.get("action") or "无")[:20],
            summary["deadline"] or "无",
        )
    st.save()
    logger.info("重生成摘要完成：成功 %d / 共 %d", done, len(rows))
    return done


def warn_consecutive_failures(store: StateStore, n: int = 3) -> list[str]:
    """连续 n 次抓取失败的站点（FR-07 简版），返回告警文案列表。"""
    alerts = []
    for site in config.SITES:
        if store.consecutive_failures(site["id"], n):
            msg = f"站点 {site['name']}({site['id']}) 连续 {n} 次抓取失败，请检查官网是否改版"
            logger.error("[告警] %s", msg)
            alerts.append(msg)
    return alerts


def preview_push(store: StateStore | None = None, limit: int = 8) -> tuple[bool, str]:
    """用真实文章渲染一条汇总并发送，不改动推送状态（用于验收排版与摘要）。"""
    from pusher import build_digest, send

    st = (store or StateStore()).load()
    rows = sorted(
        st.articles(),
        key=lambda r: parse_pub_dt(r.get("published_date")).timestamp()
        if parse_pub_dt(r.get("published_date"))
        else 0.0,
        reverse=True,
    )[:limit]
    if not rows:
        return False, "没有可预览的文章"
    title, content = build_digest(rows, total=len(rows))
    return send("【预览】" + title, content)


def main() -> int:
    ap = argparse.ArgumentParser(description="校园通知抓取一轮")
    ap.add_argument("--limit", type=int, default=None, help="每站最多处理的文章数（冒烟测试用）")
    ap.add_argument("--backfill", action="store_true", help="仅对缺摘要的历史文章补生成摘要")
    ap.add_argument("--resummarize", action="store_true", help="重抓正文并重写全部摘要（换摘要口径后用）")
    ap.add_argument("--preview-push", action="store_true", help="用真实数据发一条汇总预览，不改推送状态")
    ap.add_argument("--test-push", action="store_true", help="只发一条测试消息，验证微信推送通道")
    ap.add_argument("--export-site", action="store_true", help="只重新生成在线站点（index.html + feed.xml），不抓取")
    args = ap.parse_args()

    store = StateStore()
    if args.test_push:
        ok, msg = send_test()
        logger.info("推送测试结果：%s（%s）", "成功" if ok else "失败", msg)
        return 0 if ok else 1
    if args.export_site:
        info = site_export.export_site(store=store.load())
        logger.info("在线站点已导出：%s（%d 条通知）", info["index"], info["count"])
        logger.info("RSS 订阅源：%s", info["feed"])
        return 0
    if args.preview_push:
        ok, msg = preview_push(store)
        logger.info("推送预览结果：%s（%s）", "成功" if ok else "失败", msg)
        return 0 if ok else 1
    if args.resummarize:
        resummarize_all(config.validate_llm_config(), store=store)
    elif args.backfill:
        backfill_summaries(config.validate_llm_config(), store=store)
    else:
        run_cycle(limit=args.limit, store=store)

    # 导出审阅文件（从同一后端重新读取，保证与已落盘状态一致）
    viewer = StateStore(store.backend).load()
    count = len(viewer.articles())
    viewer.export_md(EXPORT_PATH)
    viewer.export_html(PANEL_PATH)
    # 额外写一份到项目根目录，方便直接双击打开
    try:
        viewer.export_html(QUICK_PANEL_PATH)
    except OSError as exc:  # 目录只读等情况下不影响主流程
        logger.warning("快捷面板写入失败（不影响抓取）: %s", exc)
    logger.info("审阅文件已导出: %s（共 %d 条记录）", EXPORT_PATH, count)
    logger.info("通知面板已更新: %s", PANEL_PATH)

    # 在线站点（公网分享 / RSS 订阅用），失败不影响本地面板
    try:
        info = site_export.export_site(store=viewer)
        logger.info("在线站点已更新: %s（%d 条）", info["index"], info["count"])
    except OSError as exc:
        logger.warning("在线站点导出失败（不影响抓取）: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
