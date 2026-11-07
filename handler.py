"""腾讯云函数 SCF 入口。

配置方式：SCF 控制台 → 函数配置 → 环境变量，至少设置：
    STATE_BACKEND=cos
    COS_SECRET_ID / COS_SECRET_KEY / COS_REGION / COS_BUCKET
    COS_KEY=campus-notify/state.json        # 可省略，有默认值
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
    MAX_ARTICLE_AGE_DAYS=120                # 可省略

定时触发器建议 cron（7 段，秒 分 时 日 月 周 年）：
    0 */30 * * * * *   → 每 30 分钟
"""
import json
import logging

import config
from main import run_cycle

logger = logging.getLogger("campus-notify.handler")
logger.setLevel(logging.INFO)

# 单次执行上限由环境变量 MAX_ARTICLES_PER_SITE 控制（云函数建议 6）
# 注意：SCF 默认超时 3 秒，请在控制台把超时调整到 300 秒


def main_handler(event, context):
    """SCF 事件入口。event 可为空（定时触发器）。"""
    logger.info("收到触发事件：%s", json.dumps(event, ensure_ascii=False)[:200])
    try:
        stats = run_cycle(limit=config.MAX_ARTICLES_PER_SITE)
        return {
            "ok": True,
            "stats": stats,
            "message": (
                f"新增 {stats['new']} / 更新 {stats['updated']} / "
                f"未变化 {stats['unchanged']} / 旧闻跳过 {stats['skipped_old']} / 失败 {stats['failed']}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 云函数必须返回结构化结果而非抛栈
        logger.exception("执行失败")
        return {"ok": False, "error": str(exc)}
