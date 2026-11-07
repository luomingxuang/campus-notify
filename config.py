"""集中配置：站点清单与环境变量。启动时校验，快速失败。"""
import os
import logging
from pathlib import Path

logger = logging.getLogger("campus-notify")

# ---- .env 自动加载（无第三方依赖的极简实现）----
_ENV_FILE = Path(__file__).resolve().parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

# ---- 站点清单 ----
# list_url: 列表页；article_pattern: 详情页 URL 特征；name: 展示名；short: 筛选按钮上的简称
# group: 校级 | 学院 | 部门（前端按此分组，用户勾选关注哪些来源）
# default: True 表示默认显示（校级公共信息），False 表示用户勾选后才出现
#
# 学院清单来源 https://www.hafu.edu.cn/jxyb.htm，部门来源 zzjg/xzbm.htm 与 zsdw.htm，
# 抓取日期 2026-11-06，实测脚本 scripts/probe_departments.py。
# 26 站中 25 站正文容器统一为 div.v_news_content；招生就业处个别文章正文为空（原文如此），会跳过。
SITES = [
    # ---- 校级公共（默认显示）----
    {
        "id": "school",
        "name": "学校官网·公告通知",
        "short": "学校公告",
        "group": "校级",
        "default": True,
        "list_url": "https://www.hafu.edu.cn/index/ggtz.htm",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://www.hafu.edu.cn/",
    },
    # ---- 职能部门（默认显示，学生日常最相关）----
    {
        "id": "jwc",
        "name": "教务处",
        "short": "教务处",
        "group": "部门",
        "default": True,
        "list_url": "https://jwc.hafu.edu.cn/tzgg.htm",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://jwc.hafu.edu.cn/",
    },
    {
        "id": "xsc",
        "name": "学生处（学工部）",
        "short": "学生处",
        "group": "部门",
        "default": True,
        "list_url": "https://xsc.hafu.edu.cn/index/tzgg.htm",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://xsc.hafu.edu.cn/",
    },
    {
        "id": "tw",
        "name": "团委",
        "short": "团委",
        "group": "部门",
        "default": True,
        "list_url": "https://tw.hafu.edu.cn/",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://tw.hafu.edu.cn/",
    },
    {
        "id": "zsjyc",
        "name": "招生就业处",
        "short": "招生就业",
        "group": "部门",
        "default": True,
        # 该站大量通知本体是附件（求职补贴名单、招聘简章），正文区为空。
        # 只有标题也有价值（至少知道有这件事），所以允许空正文入库，由摘要层标注说明。
        "allow_empty_content": True,
        "list_url": "https://zsjyc.hafu.edu.cn/index/tzgg.htm",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://zsjyc.hafu.edu.cn/",
    },
    {
        "id": "hqglc",
        "name": "后勤管理服务中心",
        "short": "后勤服务",
        "group": "部门",
        "default": True,
        "list_url": "https://hqglc.hafu.edu.cn/",
        "article_pattern": r"info/\d+/\d+\.htm",
        "base_url": "https://hqglc.hafu.edu.cn/",
    },
    # ---- 教学院部 20 个（默认不显示，用户选了自己的学院才出现）----
    {"id": "czswxy", "name": "财政税务学院", "short": "财税", "group": "学院", "default": False,
     "list_url": "http://czswxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://czswxy.hafu.edu.cn/"},
    {"id": "jrxy", "name": "金融学院", "short": "金融", "group": "学院", "default": False,
     "list_url": "http://jrxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://jrxy.hafu.edu.cn/"},
    {"id": "kjxy", "name": "会计学院", "short": "会计", "group": "学院", "default": False,
     "list_url": "http://kjxy.hafu.edu.cn/xyxw.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://kjxy.hafu.edu.cn/"},
    {"id": "gjmyxy", "name": "经济与贸易学院", "short": "经贸", "group": "学院", "default": False,
     "list_url": "http://gjmyxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://gjmyxy.hafu.edu.cn/"},
    {"id": "gsglxy", "name": "工商管理学院", "short": "工商", "group": "学院", "default": False,
     "list_url": "http://gsglxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://gsglxy.hafu.edu.cn/"},
    {"id": "jxxy", "name": "计算机与人工智能学院", "short": "计科", "group": "学院", "default": False,
     "list_url": "https://jxxy.hafu.edu.cn/index/ggtz.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "https://jxxy.hafu.edu.cn/"},
    {"id": "rjxy", "name": "软件学院", "short": "软件", "group": "学院", "default": False,
     "list_url": "http://rjxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://rjxy.hafu.edu.cn/"},
    {"id": "gcjjxy", "name": "工程经济学院", "short": "工程经济", "group": "学院", "default": False,
     "list_url": "http://gcjjxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://gcjjxy.hafu.edu.cn/"},
    {"id": "whcbxy", "name": "文化与传播学院", "short": "文传", "group": "学院", "default": False,
     "list_url": "http://whcbxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://whcbxy.hafu.edu.cn/"},
    {"id": "fxy", "name": "法学院", "short": "法学", "group": "学院", "default": False,
     "list_url": "http://fxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://fxy.hafu.edu.cn/"},
    {"id": "wgyxy", "name": "外国语学院", "short": "外语", "group": "学院", "default": False,
     "list_url": "http://wgyxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://wgyxy.hafu.edu.cn/"},
    {"id": "tjsxxy", "name": "统计与数学学院", "short": "统计数学", "group": "学院", "default": False,
     "list_url": "http://tjsxxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://tjsxxy.hafu.edu.cn/"},
    {"id": "jykxxy", "name": "教育科学学院", "short": "教科", "group": "学院", "default": False,
     "list_url": "http://jykxxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://jykxxy.hafu.edu.cn/"},
    {"id": "lyglxy", "name": "旅游管理学院", "short": "旅游管理", "group": "学院", "default": False,
     "list_url": "http://lyglxy.hafu.edu.cn/tzgg/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://lyglxy.hafu.edu.cn/"},
    {"id": "hjjjxy", "name": "环境经济学院", "short": "环境经济", "group": "学院", "default": False,
     "list_url": "http://hjjjxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://hjjjxy.hafu.edu.cn/"},
    {"id": "tyyjkglxy", "name": "体育与健康管理学院", "short": "体育健康", "group": "学院", "default": False,
     "list_url": "http://tyyjkglxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://tyyjkglxy.hafu.edu.cn/"},
    {"id": "yssjxy", "name": "艺术设计学院", "short": "艺术设计", "group": "学院", "default": False,
     "list_url": "http://yssjxy.hafu.edu.cn/", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://yssjxy.hafu.edu.cn/"},
    {"id": "yyxy", "name": "音乐学院", "short": "音乐", "group": "学院", "default": False,
     "list_url": "http://yyxy.hafu.edu.cn/index/xyxw.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://yyxy.hafu.edu.cn/"},
    {"id": "gjjyxy", "name": "国际教育学院", "short": "国际教育", "group": "学院", "default": False,
     "list_url": "http://gjjyxy.hafu.edu.cn/index/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://gjjyxy.hafu.edu.cn/"},
    {"id": "mkszyxy", "name": "马克思主义学院", "short": "马克思", "group": "学院", "default": False,
     "list_url": "http://mkszyxy.hafu.edu.cn/tzgg.htm", "article_pattern": r"info/\d+/\d+\.htm",
     "base_url": "http://mkszyxy.hafu.edu.cn/"},
]

# 学院清单（供前端生成「选我的学院」下拉与按学院分享的链接）
COLLEGES = [s for s in SITES if s.get("group") == "学院"]
DEFAULT_SITE_IDS = [s["id"] for s in SITES if s.get("default")]

CRAWL_INTERVAL_MINUTES = int(os.environ.get("CRAWL_INTERVAL_MINUTES", "30"))

# 数据目录（本地后端与导出文件）
DATA_DIR = Path(__file__).resolve().parent / "data"

# 项目根目录（把可视化面板额外放一份到一眼能看到的位置，方便双击打开）
PROJECT_ROOT = DATA_DIR.parent.parent.parent

# 在线站点导出目录（纯静态站：index.html + feed.xml，可直接托管到任何静态服务）
# 云端 CI 里用 SITE_DIR=docs 覆盖，便于 GitHub Pages 直接以 /docs 作为发布源
SITE_DIR = Path(os.environ.get("SITE_DIR") or (PROJECT_ROOT / "4-交付物" / "在线站点"))

# 站点对外访问地址（部署后回填，用于 RSS 里的绝对链接与自引用地址）
# 例：https://xxx.github.io/campus-notify
SITE_PUBLIC_URL = os.environ.get("SITE_PUBLIC_URL", "").strip().rstrip("/")

# 托管平台要求的访问凭证（WorkBuddy 发布链接形如 https://xxx/?sharecode=ABC，
# 静态资源不带该参数会返回 403，因此 RSS 地址必须拼上它）
SITE_SHARE_CODE = os.environ.get("SITE_SHARE_CODE", "").strip()

# ---- 云服务（WorkBuddy Cloud Service）----
# 云服务已开通（资源 wbcs_qgtWzc2imdM4ZoNbTHSHI2），但**前端当前未接入**：
# 通知页加「登录才能收藏」的门槛，代价大于收益，收藏已改为浏览器本地存储（见 fav_local.py）。
# 这三项保留，供将来做「订阅名单 / 用户反馈」这类确实需要后端的功能时启用。
#
# 云服务能力边界（已核实）：database / auth / storage / llm，**没有边缘函数、没有定时任务、
# 不能主动访问外网**。所以它跑不了爬虫，抓取仍靠 GitHub Actions 或本机计划任务。
CLOUD_ENDPOINT = os.environ.get(
    "CLOUD_ENDPOINT", "https://campus-notify.app.workbuddy.host"
).strip().rstrip("/")
CLOUD_OAUTH_RELAY = os.environ.get(
    "CLOUD_OAUTH_RELAY", "https://www.workbuddy.cn/v2/as/genie-baas/oauth"
).strip()
CLOUD_PUBLISHABLE_KEY = os.environ.get(
    "CLOUD_PUBLISHABLE_KEY",
    "wbpk_m4u3fhWji48VIfMudNuDNr_UEq1DxfajfiC71p8UyL8rpTYfm2H9r4p",
).strip()

# 状态后端：local（本地文件）| cos（腾讯云 COS，云函数使用）
STATE_BACKEND = os.environ.get("STATE_BACKEND", "local")

# 时效过滤：发布日期早于 N 天的文章不入库（避免列表页历史旧闻被推送）
MAX_ARTICLE_AGE_DAYS = int(os.environ.get("MAX_ARTICLE_AGE_DAYS", "120"))

# 抓取限速：每次请求间隔秒数，对官网友好
REQUEST_DELAY_SECONDS = 2.0
REQUEST_TIMEOUT_SECONDS = 15
RETRY_TIMES = 1  # 失败重试次数

# 每轮每站最多抓取的详情页数量（控制对官网压力与 LLM 成本；云函数建议 6）
MAX_ARTICLES_PER_SITE = int(os.environ.get("MAX_ARTICLES_PER_SITE", "10"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 campus-notify/0.1"
)

# ---- LLM 配置（OpenAI 兼容接口；缺省则摘要降级）----
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "")


def validate_llm_config() -> bool:
    """校验 LLM 配置是否可用；不可用则降级并提示（不算致命错误）。"""
    if LLM_BASE_URL and LLM_API_KEY and LLM_MODEL:
        return True
    logger.warning("LLM 配置不完整（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL），摘要将降级为标题+链接输出")
    return False


# ---- 推送配置（缺省则只在本地生成面板，不推送）----
# 通道，可逗号组合多个，例如 PUSH_CHANNEL=serverchan,windows
#   serverchan（Server酱，微信扫码即可，免费 5 条/天）
#   pushplus（推送加，免费 200 条/天，需实名）
#   windows（本机 Windows 右下角系统通知：纯本地、免费、无广告，只要电脑开着就能收到）
#   off / 留空 = 不推送
PUSH_CHANNELS = [
    c.strip() for c in os.environ.get("PUSH_CHANNEL", "").split(",") if c.strip()
]
# 兼容旧代码里 PUSH_CHANNEL 的单通道写法
PUSH_CHANNEL = PUSH_CHANNELS[0] if PUSH_CHANNELS else ""
SERVERCHAN_SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "").strip()

# 推送模式：daily（每天固定时间推一条汇总，默认）| instant（有新通知就推）
PUSH_MODE = os.environ.get("PUSH_MODE", "daily").strip().lower()
# daily 模式下最早推送的小时（0-23）。电脑未开机则当天首次开机后补推。
PUSH_DAILY_HOUR = int(os.environ.get("PUSH_DAILY_HOUR", "8"))
# 每天最多推送条数（防止异常刷屏 + 免费额度保护）
PUSH_DAILY_LIMIT = int(os.environ.get("PUSH_DAILY_LIMIT", "5"))
# 单条推送最多列出的通知条数（超出只报总数，原文在面板里看）
PUSH_MAX_ITEMS = int(os.environ.get("PUSH_MAX_ITEMS", "8"))
# 推送时效窗口：只推「发布日期在最近 N 天内」的通知，更老的静默归档（避免首次配置时刷屏）
PUSH_MAX_AGE_DAYS = int(os.environ.get("PUSH_MAX_AGE_DAYS", "7"))


def enabled_channels() -> list[str]:
    """返回真正可用的推送通道列表（凭据缺失或名称不认识的会被剔除并告警）。"""
    usable = []
    for name in PUSH_CHANNELS:
        if name in ("", "off", "none"):
            continue
        if name == "serverchan" and not SERVERCHAN_SENDKEY:
            logger.warning("PUSH_CHANNEL 含 serverchan 但未配置 SERVERCHAN_SENDKEY，跳过该通道")
            continue
        if name == "pushplus" and not PUSHPLUS_TOKEN:
            logger.warning("PUSH_CHANNEL 含 pushplus 但未配置 PUSHPLUS_TOKEN，跳过该通道")
            continue
        if name not in ("serverchan", "pushplus", "windows"):
            logger.warning(
                "未知推送通道 %s（支持 serverchan / pushplus / windows），跳过该通道", name
            )
            continue
        usable.append(name)
    return usable


def validate_push_config() -> bool:
    """校验推送配置；一个可用通道都没有时返回 False（不阻塞抓取）。"""
    return bool(enabled_channels())
