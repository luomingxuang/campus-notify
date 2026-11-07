"""AI 摘要模块：OpenAI 兼容接口；未配置 LLM 时降级输出，不阻塞主流程。"""
import json
import logging

import requests as http_client

import config
from state import content_hash

logger = logging.getLogger("campus-notify.summarizer")

CATEGORIES = ["教务", "通知", "活动", "财务", "其他"]
AUDIENCES = ["全校学生", "学生", "教师", "特定年级", "特定学院", "不限"]

PROMPT_INSTRUCTION = """你是校园通知助理，服务对象是本校学生。只依据原文信息填写，原文没有的一律不写、不推测。输出 JSON：
{"headline": "", "points": [], "action": "", "audience": "", "category": "", "deadline": ""}

字段要求：
- headline：一句话说清「这是什么」，14-22 字。要具体到「谁 / 什么事」，让人一眼判断跟自己有没有关系。
  好的例子：「2026 届毕业生求职补贴开始申领」「图书馆 11 月 10 日起闭馆改造两个月」「教学计划 11 月 13 日前须完成录入」
  坏的例子：「关于做好相关工作的通知」（空壳）、「学院召开会议」（等于没说）。
  禁止照抄标题，禁止用「本次通知」「旨在」「进一步」「相关」「有关事项」这类词。
- points：2-3 条关键要点，每条 12-24 字，只写学生真正要用的信息：谁需要办、什么时间、什么地点、要交什么、有什么条件或限制。
  每条都必须是原文里的具体事实（带数字、日期、地点、金额、门槛）。没有就说少点，**禁止为了凑数编造或写空话**。
  资讯/新闻类（获奖、会议、结业）最多 1 条，只写「谁、什么赛事或事项、结果」，不写流程细节。
- action：学生需要做的具体动作，一句话，带时间和入口，例如「11 月 13 日前登录教务系统核对教学计划」。
  纯资讯、结果公示、新闻类，学生无需行动，填空字符串 ""。
- audience：这条通知主要面向谁，只能取：全校学生 / 学生 / 教师 / 特定年级 / 特定学院 / 不限
- category：只能取：教务 / 通知 / 活动 / 财务 / 其他
- deadline：学生需要提交材料、报名、缴费、申请的截止时间，格式 YYYY-MM-DD HH:MM。
  如果只有活动起止、展览周期、会议时间而学生无需提交任何东西，填空字符串 ""。
- deadline 与活动时间段要区分：例如「活动 11 月 1 日至 30 日」不是 deadline，「11 月 20 日前提交作品」才是 deadline。

**绝对不要写进 headline 和 points 的内容**（这是最常见的废话来源）：
1. 领导姓名与职务、出席人员名单、参会部门清单
2. 会议流程、讲话顺序、汇报了几项工作
3. 「为了…」「旨在…」「进一步加强…」「推动…再上新台阶」这类套话
4. 「详见原文」「具体以通知为准」——这些读者自己知道

判断示例（仅示范口径，不要输出）：
- 原文「各学院须于 11 月 13 日 15:00 前完成教学计划录入，教务处于 14 日汇总」→
  headline「教学计划 11 月 13 日前须完成录入」，points「面向各学院教学秘书与任课教师」「13 日 15:00 截止，14 日教务处汇总」，action「11 月 13 日 15:00 前完成教学计划录入与核对」，audience「教师」，deadline「2026-11-13 15:00」
- 原文「图书馆 11 月 10 日起闭馆改造，预计工期两个月，闭馆期间借阅服务暂停，自习区移至第三教学楼」→
  headline「图书馆 11 月 10 日起闭馆改造两个月」，points「借阅服务暂停，已借图书自动续期」「自习区移至第三教学楼一层」，action「闭馆前取回个人物品，自习改去第三教学楼」，audience「全校学生」，deadline「2026-11-10 00:00」
- 原文「我校学生在全国大学生数学建模竞赛中获一等奖 2 项」→
  headline「我校数学建模竞赛获 2 项一等奖」，points「全国大学生数学建模竞赛」「获奖学生来自统计与数学学院」，action ""，audience「学生」，deadline ""

只输出 JSON，不要任何解释文字。"""


def build_prompt(title: str, content: str) -> str:
    """用字符串拼接组装 prompt（避免 format 与 JSON 大括号冲突）。"""
    if not (content or "").strip():
        body = (
            "（原文正文区为空，具体内容很可能是附件或图片，抓不到。"
            "只能依据标题判断这件事是什么，不要编造内容、时间、地点、金额。）"
        )
    else:
        body = content
    return f"{PROMPT_INSTRUCTION}\n\n通知标题：{title}\n通知正文（节选）：\n{body}"


def summarize(article: dict, llm_enabled: bool) -> dict:
    """返回 {headline, points, summary, action, audience, category, deadline, content_empty}。

    `summary` 是由 headline + points 拼出的完整句子，只用于搜索、导出 md 与推送，
    页面渲染走 headline + points（可扫读）。LLM 不可用或调用失败时降级为空。
    """
    empty = {
        "headline": "",
        "points": [],
        "summary": "",
        "action": "",
        "audience": "",
        "category": "",
        "deadline": "",
        "content_empty": False,
    }
    if not llm_enabled:
        return empty
    try:
        resp = http_client.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": config.LLM_MODEL,
                "messages": [{"role": "user", "content": build_prompt(
                    article["title"], article["content"][:4500]
                )}],
                "temperature": 0.2,
            },
            timeout=40,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        # 容错：剥掉可能的 ```json 包裹
        text = text.strip("` \n")
        if text.startswith("json"):
            text = text[4:]
        data = json.loads(text)
        if data.get("category") not in CATEGORIES:
            data["category"] = "其他"
        audience = (data.get("audience") or "").strip()
        if audience not in AUDIENCES:
            audience = "不限"

        headline = (data.get("headline") or "").strip()[:48]
        raw_points = data.get("points") or []
        if isinstance(raw_points, str):  # 模型偶尔会返回字符串
            raw_points = [p for p in raw_points.replace("；", "\n").split("\n")]
        points, seen = [], set()
        for p in raw_points if isinstance(raw_points, list) else []:
            p = str(p).strip().lstrip("·-• ").strip()[:60]
            if p and p not in seen:
                seen.add(p)
                points.append(p)
        points = points[:3]

        # 兼容字段：搜索、导出 md、微信推送都吃 summary 这一个字符串
        summary = headline
        if points:
            summary = f"{headline}。" + "；".join(points) if headline else "；".join(points)
        summary = summary.strip("。 ")[:200]

        return {
            "headline": headline,
            "points": points,
            "summary": summary,
            "action": (data.get("action") or "").strip()[:120],
            "audience": audience,
            "category": data["category"],
            "deadline": (data.get("deadline") or "").strip(),
            # 正文为空不再塞进摘要正文，改成独立标记，由页面/推送单独提示
            "content_empty": not (article.get("content") or "").strip(),
        }
    except Exception as exc:  # noqa: BLE001 降级不阻塞
        logger.warning("摘要生成失败，降级输出（标题+链接）：%s", exc)
        return empty
