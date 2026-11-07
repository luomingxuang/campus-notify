# campus-notify · 抓取+去重+摘要框架

校园通知提醒：抓取河南财政金融学院 26 个二级站点（学校公告 + 20 个学院 + 5 个部门）公开通知 → 正文哈希去重 → LLM 摘要（可降级）→ 导出审阅文件与在线站点。
同时支持**本地运行**与**腾讯云函数 SCF 运行**（状态存 COS，无需本机常开）。

## 运行

```bash
pip install -r requirements.txt

python main.py               # 本地跑一轮（每站最多 10 篇）
python main.py --limit 2     # 冒烟（每站 2 篇）
python main.py --backfill    # 只给历史缺摘要的文章补生成
python main.py --resummarize # 重抓正文并重写全部摘要（改了摘要口径后用）
python main.py --preview-push # 用真实数据发一条汇总预览，不改推送状态
python main.py --test-push   # 只发一条测试消息，验证微信推送通道

# 云端部署（一条命令搞定建桶 + 建函数 + 环境变量 + 定时触发器 + 首轮验证）
python scripts/deploy_scf.py --dry-run   # 先检查配置
python scripts/deploy_scf.py             # 正式部署
python scripts/pull_state.py             # 把云端状态拉下来导出 Markdown

# 自检
python scripts/verify_cos_sign.py        # COS 签名与官方 SDK 交叉验证
python scripts/verify_purepy.py          # 部署包在无平台二进制条件下可运行
```

LLM 摘要：复制 `.env.example` 为 `.env`，填入 DeepSeek 密钥（base_url/model 已预填）；未填 key 时自动降级为「标题+链接」，不影响抓取。

云函数部署：完全自动，只需 `.deploy.env`（从 `.deploy.env.example` 复制）填腾讯云 SecretId/SecretKey。操作步骤见 `1-文档/云函数部署操作卡.md`，细节见 `1-文档/腾讯云函数部署说明.md`。

## 结构

| 文件 | 职责 |
|------|------|
| config.py | 站点清单、限速参数、LLM 配置、`.env` 加载（集中校验） |
| fetcher.py | HTTP 抓取：限速 2s/次、失败重试 1 次、统一 UA |
| parser.py | 博达 CMS 解析：列表页提链接、详情页提标题/日期/正文、标题清洗 |
| state.py | 状态层：JSON 快照 + 可切换后端（本地文件 / 腾讯云 COS），去重键 = 站点+URL+正文哈希；含 Markdown 与可视化面板渲染 |
| cos_client.py | 极简 COS 客户端（纯标准库签名，**不依赖** cos-python-sdk-v5/pycryptodome，解决跨平台二进制问题） |
| summarizer.py | LLM 摘要（OpenAI 兼容），输出 摘要/要做的事/面向人群/类别/截止时间，失败降级 |
| pusher.py | 微信推送（Server酱 / PushPlus）：每日汇总、按发布时间倒序、快截止置顶、每日额度保护、老通知归档 |
| main.py | 单轮任务编排 + 时效过滤 + 连续 3 次失败告警 |
| handler.py | 腾讯云函数入口 `handler.main_handler` |
| scripts/build_scf_package.py | 打包 zip（代码 + 纯 Python 依赖，剔除所有平台二进制） |
| scripts/local_run.py | 本地计划任务入口（pythonw 无窗口，日志写 data/logs/crawl.log） |
| scripts/deploy_scf.py | 一键部署：COS 桶 → 云函数 → 环境变量 → 定时触发器 → 首轮验证 |
| scripts/pull_state.py | 拉取云端状态快照，导出 `4-交付物/云端通知摘要.md` |
| scripts/verify_cos_sign.py | 签名交叉验证（自研 vs 官方 SDK，逐字节比对） |
| scripts/verify_purepy.py | 部署包纯净性验证（模拟 Linux 无二进制环境） |
| .github/workflows/crawl.yml | GitHub Actions 定时任务（每 2 小时，跑完提交状态快照） |

本地产物在 `data/`：`state.json`（去重状态）、`outputs/最新通知摘要.md`（人工审阅用）。
云端状态存在 COS 的 `campus-notify/state.json`。

## 已验证（2026-11-06）

- 4 站点全通，解析文章入库（标题/日期/正文齐全）
- 去重生效：重跑「新增 0 / 未变化 N / 失败 0」
- 标题清洗（去掉 CMS 拼接的站点后缀）
- **LLM 摘要实测（DeepSeek deepseek-chat）**：33 篇全部生成摘要（33/33），类别与截止日期抽取可用
  - 样例：「关于录入和核对2026-2027学年第2学期教学计划」→ 截止 2026-11-13 15:00
- 时效过滤：发布日期早于 `MAX_ARTICLE_AGE_DAYS`（默认 120 天）的文章跳过
- 云函数包验证：解压到独立目录后 `main_handler` 可直接执行成功
- **跨平台打包修复**：原包 8.17 MB，混入 110 个 Windows `.pyd`（pycryptodome/charset_normalizer），到 Linux 必崩。改为自研纯标准库 COS 客户端 + 剔除二进制后 **0.7 MB、0 个二进制**，实测 HTTPS 抓取 200、全模块导入通过
- **COS 签名交叉验证**：自研签名与官方 SDK 逐字节一致（GET/PUT 均通过）
- 部署脚本 `--dry-run` 与缺凭证提示已验证

## 云端运行：GitHub Actions（当前主方案）

`.github/workflows/crawl.yml` 已就绪，每 2 小时跑一轮，状态 `data/state.json` 每轮提交回仓库（去重 + 保持仓库活跃）。

- 部署步骤见 `1-文档/GitHub-Actions部署操作卡.md`
- 密钥放仓库 Secrets（`LLM_API_KEY`），不进代码
- 频率调整：改 workflow 里 `cron: '0 */2 * * *'`
- 备用方案：腾讯云函数 SCF（`scripts/deploy_scf.py` 一键部署，因个人实名要求满 18 周岁暂未启用）

## 查看通知：可视化面板（本地）

每轮抓取完成后自动生成两个文件：

| 文件 | 用途 |
|------|------|
| `项目根/看最新通知.html` | **双击就能看**的通知面板（手机浏览器也能开） |
| `data/outputs/通知面板.html` | 同上，运行时产物原位置 |
| `data/outputs/最新通知摘要.md` | 纯文本版，方便复制粘贴 |

面板能力：

- **默认按发布日期从新到旧**，卡片顶部显示 `📅 11-06 16:24 · 今天`（当日/昨天/前天/N 天前）
- 右上角可切换排序：**最新发布** / **最近截止**（前端重排，无需重新抓取）
- 截止徽章（7 天内橙色/3 天内红色/已截止灰色）、类别筛选、站点筛选、关键词搜索（含摘要与「要做的事」）
- 有明确行动的条目用橙色块标出 **要做的事**
- **整张卡片可点**：点卡片任意位置（正文、要点、箭头 `↗`）都打开官网原文；
  正在选中文字、按修饰键、点「☆ 收藏」时不跳，避免打断复制或误跳（`scripts/check_card_click.js` 17 项断言守着）
- **摘要分两层**：一句话结论（`headline`，判断跟自己有没有关系）+ 2-3 条要点（`points`，谁/何时/何地/要交什么）；
  正文是图片附件的条目单独打「📎 原文正文是图片或附件」提示，不再把提示语塞进摘要正文
- 单文件、零外部依赖、离线可用

## 公开分享：在线站点 + RSS（多用户）

面板只有本机能看。要让同学也能看，跑一次 `python main.py --export-site`（正常抓取流程末尾也会自动执行），
会在 `4-交付物/在线站点/` 生成两个纯静态文件：

| 文件 | 用途 |
|------|------|
| `index.html` | 公网版面板（在线版，带订阅说明、RSS 自动发现链接） |
| `feed.xml` | RSS 2.0 订阅源，任何阅读器可订阅，含摘要/要做的事/截止 |

目录是纯静态的，可直接托管到任意静态服务（GitHub Pages / 对象存储 / 反向代理）。

**关键：RSS 地址必须拼上访问码**

托管平台（如 WorkBuddy 发布链接）用 `?sharecode=xxx` 做访问控制，静态资源不带该参数会返回 403。
所以 `.env` 里要配：

```
SITE_PUBLIC_URL=https://your-domain      # 站点根地址
SITE_SHARE_CODE=xxxxxx                   # 访问码，无访问控制时留空
```

导出时会自动拼成 `https://your-domain/feed.xml?sharecode=xxxxxx`，写进页面的订阅说明与 RSS 自引用，
同学复制即可用。

**为什么要单独做在线版**：`--export-site` 的产物含绝对地址，本地双击的 `看最新通知.html` 用相对路径，两者互不影响。

## 关注来源：全校 26 站，默认窄

站点从 4 个扩到 **26 个**（学校公告 + 20 个教学院部 + 教务/学生处/团委/招生就业/后勤 5 个部门）。
每站在 `config.SITES` 里带三个关键字段：

| 字段 | 作用 |
|---|---|
| `group` | `校级` / `部门` / `学院`，前端按此分组 |
| `short` | 筛选面板上的简称（「计科」「教务」），避免 26 个全长名挤爆 |
| `default` | `True` 表示默认显示，即校级公共的 6 站 |

**默认只显示校级 6 站**。20 个学院默认全关 —— 计科学生不需要看音乐学院的演出通知。
用户选了「我的学院」才会把本院加进来，选择存 `localStorage`（键 `campusNotify.sources.v1`），
仍然零登录。

**按学院分享**：链接加 `?dept=jxxy` 打开就是「校级 + 计科」视图，适合往各院群里发。
URL 参数**不写入本地偏好** —— 点别人的分享链接不该改掉自己的设置，页面会给一条「设为我的学院」的提示。

站点清单来源与逐站实测结果见 `1-文档/全校推广可行性评估.md`；
探测脚本 `scripts/probe_departments.py`，官网调整机构后重跑即可。

**已知限制**：官网混用两套 CMS，`nry.jsp?wbtreeid=...` 形式的文章（工程经济学院、艺术设计学院较多）
现有规则匹配不上，会被跳过。招生就业处大量通知正文为空（内容在附件里），已单独允许空正文入库，
摘要会明确标注「原文正文区为空，请点开原文」。

## 收藏功能：本地存储，不登录

在线版每条通知右下角有「☆ 收藏」，点一下存进浏览器 `localStorage`（键名 `campusNotify.favs.v1`），
再用工具栏「☆ 我的收藏（N）」筛出来看，等于一份自己攒的待办清单。

**刻意不做登录**：通知页的核心动作是「看」，加登录等于给第一次访问的人设门槛，而收藏这个量级的
功能不值得付出注册、授权、会话过期、回跳丢参数这一整套代价。代价如实写在页面上：收藏只在本机浏览器，
换设备或清缓存会丢。

代码在 `fav_local.py`（样式 + 脚本），`state.py` 在 `online=True` 时注入；本地双击的 `看最新通知.html`
完全不含收藏代码。

## 云服务：已开通，但当前不走后端

云服务（`wbcs_qgtWzc2imdM4ZoNbTHSHI2`）已开通、状态正常，表 `favorites` 与三条 owner-only RLS 策略
都保留着，但**前端没有接入**——现阶段用不上。

**为什么云服务不能替代爬虫**（重要，别再重复讨论）：云服务提供的是 `database / auth / storage / llm`，
**没有边缘函数、没有定时任务、不能主动访问外网**。它的每一次工作都由「有人访问页面」触发，没人访问就什么
也不干。定时爬虫需要「能执行代码 + 能定时唤醒 + 能主动抓网页」三件事，靠的是 GitHub Actions
（代码已就绪，待建仓库）或本机计划任务，不是云服务。

云服务真正会用上的场景：**订阅名单**——同学各自收微信提醒时要存各自的 SendKey，必须做到别人看不到、
改不了，那时登录才是有理由的。判断标准是「数据必须跟着人走，而不是跟着设备走」。
详见 `1-文档/云服务与收藏说明.md`。

## 本机定时任务（兜底，已配好）

Windows 计划任务 `CampusNotifyCrawl`，每 30 分钟静默跑一轮（无黑窗口）。

```powershell
Start-ScheduledTask -TaskName "CampusNotifyCrawl"      # 立刻跑一次
Get-ScheduledTaskInfo -TaskName "CampusNotifyCrawl"    # 看上次结果/下次时间
Unregister-ScheduledTask -TaskName "CampusNotifyCrawl" -Confirm:$false   # 上云后删除
```

- 日志：`data/logs/crawl.log`（超 2MB 自动滚存为 `.log.1`）
- 只在电脑开机且已登录时运行；上云后本任务可删，两者不冲突
- `LastTaskResult = 267009` 表示「正在运行」，`0` 表示成功
- 想看最新结果：双击项目根目录的 `看最新通知.html`，按 F5 刷新即可（每次抓取后自动覆盖）

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 空 | 缺任一项则降级（不阻塞抓取） |
| `STATE_BACKEND` | `local` | `local` 本地文件 / `cos` 腾讯云 COS |
| `COS_SECRET_ID` / `COS_SECRET_KEY` / `COS_REGION` / `COS_BUCKET` | 空 | `STATE_BACKEND=cos` 时必填 |
| `COS_KEY` | `campus-notify/state.json` | 状态对象路径 |
| `MAX_ARTICLES_PER_SITE` | `10` | 每站每轮最多抓取篇数（云函数建议 6） |
| `MAX_ARTICLE_AGE_DAYS` | `120` | 旧闻过滤天数 |
| `PUSH_CHANNEL` | 空 | `serverchan` / `pushplus` / 空=不推送 |
| `SERVERCHAN_SENDKEY` | 空 | Server酱 SendKey（https://sct.ftqq.com 微信扫码即得，免费 5 条/天） |
| `PUSHPLUS_TOKEN` | 空 | PushPlus token（备选通道，需实名） |
| `PUSH_MODE` | `daily` | `daily` 每天一条汇总 / `instant` 有新就推 |
| `PUSH_DAILY_HOUR` | `8` | daily 模式最早推送小时（错过会当天补推） |
| `PUSH_DAILY_LIMIT` | `5` | 每日最多推送条数 |
| `PUSH_MAX_ITEMS` | `8` | 单条消息最多列几条通知 |
| `PUSH_MAX_AGE_DAYS` | `7` | 超出此天数的老通知不推（静默归档） |

## 微信推送

配置步骤见 `1-文档/微信推送配置操作卡.md`。核心设计：

- 推送是**附加能力**，未配置时静默跳过，推送失败绝不影响抓取与面板生成
- 每篇通知有 `pushed` 标记，推送成功后置位，保证不会重复推
- `push_history` 记录每日推送次数，用于额度保护 + daily 模式「一天只推一条」
- 时效窗口内的才推，超出 `PUSH_MAX_AGE_DAYS` 的静默归档（首次配置不会刷屏）
- **正文按发布时间从新到旧排列**，每条标 `📅 11-06 16:24（今天）`
- 3 天内截止的单独置顶「⏰ 快截止（别漏）」，避免按时间排序后被挤出列表
- 每条含 AI 摘要 + `👉 要做` 行动项 + 截止时间 + 原文链接；纯资讯类（获奖、会议）不显示行动项

## 待办（下一阶段）

- [x] LLM 摘要实测 + 截止日期抽取（DeepSeek，33/33）
- [x] 云端运行改造（SCF + COS 状态持久化 + 打包脚本 + 部署文档）
- [x] 一键部署脚本（`scripts/deploy_scf.py`，无需控制台点击）
- [x] 本机兜底：Windows 计划任务每 30 分钟静默运行（`scripts/local_run.py`）
- [x] 本地可视化面板：每轮自动生成 `看最新通知.html`（发布时间倒序 / 排序切换 / 徽章 / 搜索 / 类别筛选）
- [x] **摘要口径升级（2026-11-06 晚）**：新增「要做的事 action」「面向人群 audience」字段；deadline 只取学生需提交/报名/缴费的截止，不再把活动起止时间误当截止；发布时间精度提升到时分。40 篇已全量重写
- [x] 微信推送（`pusher.py`）：Server酱 / PushPlus 双通道，daily 汇总模式，额度保护与去重推送
- [x] **微信推送已实测跑通（2026-11-06 20:18）**：测试消息成功、真实汇总推送成功（12 条通知），复跑「没有待推送的通知」——每日额度与去重均生效
- [ ] 用户提供腾讯云 SecretId/SecretKey 后执行部署与验证
- [ ] 截止日期准确性抽查（抽 5 篇人工核对正文日期）
- [ ] 公众号模板消息推送接入（FR-04/05）
- [ ] 小程序列表页（FR-06）
