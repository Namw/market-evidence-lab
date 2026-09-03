# Market Evidence Lab

Market Evidence Lab 是一个聚焦 ETHUSDT 市场数据与新闻事件观察的 Django 项目。

## 当前范围

项目包含四组产品能力：

1. 采集与调度
   - Binance USD-M Futures ETHUSDT 1d / 1h / 5m K线采集。
   - 1h / 5m OI 与实际 Funding 结算数据采集。
   - Deribit ETH DVOL、活跃期权合约元数据与期权行情快照采集。
   - 多新闻源采集，包括 Federal Reserve 货币政策 RSS 与 BLS 非农、CPI、PPI RSS。
   - 采集质量检查、调度配置和运行记录。
2. 新闻观察
   - 新闻原始数据查看与手工采集。
   - ETH 方向分类。
   - 客观事实提取与校验。
   - 多来源新闻归并为暂定事件库。
3. 行情数据观察
   - 联动展示日 K、小时 K、OI 与 Funding；点击1h K线查看对应的5m K线与5m OI。
4. ETH 资金观察
   - DeFiLlama Ethereum 稳定币总供应日频历史与重叠更新。
   - Farside ETH ETF 每日资金流、动态 ticker 与修订回刷。
   - 公开地址快照模型与可复算变化指标；Etherscan 自动采集因当前来源条款而保持阻止状态。

市场异常巡检、研究案例、价格证据、衍生品证据、独立手工行情采集页和独立质检页已移除。

## 页面

- 首页：<http://127.0.0.1:8001/>
- 自动调度：<http://127.0.0.1:8001/system/schedules/>
- 调度情况：<http://127.0.0.1:8001/system/schedules/runs/>
- 新闻数据采集：<http://127.0.0.1:8001/collection/news/>
- 新闻分析：<http://127.0.0.1:8001/analysis/news/>
- 客观事实提取：<http://127.0.0.1:8001/analysis/news/objective-facts/>
- 新闻事件库：<http://127.0.0.1:8001/analysis/news/events/>
- 行情数据查看：<http://127.0.0.1:8001/market-data/>
- ETH 资金观察：<http://127.0.0.1:8001/market-funds/>

以上页面的详情、运行、筛选和操作子路径仍属于对应功能的一部分。

## 技术栈

- Python 3.12
- Django 5.2
- PostgreSQL 16
- `uv`

## 本地启动

```bash
docker compose up -d
uv sync
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py runserver 8001
```

自动任务需要单独启动调度执行器：

```bash
uv run --env-file .env python manage.py run_scheduler
```

Binance 公告与 BLS RSS 默认显式直连，不使用系统环境代理。若当前网络无法访问，可在 `.env` 配置本地代理：

```dotenv
SOURCE_PROXY_URL=http://127.0.0.1:7897
```

手动新闻任务可按次勾选是否使用该代理；每日官方新闻自动任务也可独立保存该选项。代理只作用于 Binance 公告与 BLS RSS，Fed 与其他新闻源保持直连；未勾选时不会自动采用降级数据源。

Deribit 期权采集会同步合约元数据，并在同一轮保存 IV、期权 OI、价格与成交量快照：

```bash
# 默认回看最近3天的小时 DVOL，并保存当前市场快照（时间按5分钟对齐）
uv run --env-file .env python manage.py collect_deribit_options

# 首次回补 ETH DVOL；期权链历史仍从执行时刻开始积累
uv run --env-file .env python manage.py collect_deribit_options --dvol-start 2021-03-24

# 只补采当前合约和行情快照时，可跳过 DVOL 回补
uv run --env-file .env python manage.py collect_deribit_options --skip-dvol

# 接入现有常驻调度器，每天 08:20（Asia/Shanghai）自动执行一次
uv run --env-file .env python manage.py configure_deribit_options_schedule --enable --run-time 08:20
```

自动调度默认关闭，启用后由 `run_scheduler` 领取任务；可使用同一命令的
`--disable` 参数停用。

ETH 资金观察数据可手工初始化或回刷；相同数据会幂等跳过，历史修订会更新：

```bash
uv run --env-file .env python manage.py collect_market_funds stablecoin
uv run --env-file .env python manage.py collect_market_funds etf
uv run --env-file .env python manage.py collect_market_funds addresses
```

`addresses` 命令只记录来源策略阻止状态，不会向 Etherscan 发出自动采集请求。

## BSC Meme 新币监听 MVP

`apps/meme_monitor` 是独立的 DEX 新 Pair 研究模块。第一版使用无需认证的
GeckoTerminal Public API，通过 Adapter 将外部响应标准化后，以价格、5分钟成交额、
交易数和流动性四项规则检测异常。采集过程在内存中计算；PostgreSQL 只保存每个 Pair
的当前状态、重启所需的有界成交额基线、异常事件与研究 Episode，不再追加原始行情快照。
异常事件使用 UUID `event_id`，可供后续研究关联。

先执行迁移，并启动项目统一调度执行器：

```bash
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py run_scheduler
```

随后可在 Meme 总览页使用“启动定时检查 / 关闭定时检查”开关。启用后计划立即到期，
由在线的统一执行器领取；关闭后不再产生新轮次，已开始的当前轮次会执行完成。调度器
默认每30秒检查到期计划。单轮联调仍可使用：

```bash
uv run --env-file .env python manage.py run_meme_monitor --once
```

观察页分为三个子页面，均按轮询间隔自动刷新：

- 总览：<http://127.0.0.1:8001/meme-monitor/>，展示 heartbeat、摘要指标与最近轮次；
- 异常事件与后续表现：<http://127.0.0.1:8001/meme-monitor/anomalies/>；
- 最新跟踪 Pair：<http://127.0.0.1:8001/meme-monitor/pairs/>；
- 首次异常 5 分钟延续性研究：<http://127.0.0.1:8001/meme-monitor/research/>。

总览页的开关只修改当前 `.env` 所连接数据库中的计划状态，不会从 Web 进程创建系统
后台进程；因此页面会同时显示统一调度执行器是否在线。也可直接运行
`run_meme_monitor` 作为不受页面开关控制的独立常驻调试进程。

默认监听 BSC、Pair 最大年龄24小时、每30秒轮询、同一
`token_address + anomaly_type` 冷却10分钟。阈值和网络均通过 `.env` 的
`MEME_MONITOR_*` 参数配置，完整默认值见 `.env.example`。第一版异常成立条件为：

- 5分钟价格涨幅不低于30%；
- 5分钟成交额不低于5,000 USD；
- 5分钟买卖交易合计不低于20笔；
- 流动性不低于5,000 USD。

本地历史达到最小样本数后，还会计算当前5分钟成交额相对此前均值的倍数，并将达到
3倍的事件额外标记为 `volume_spike`；该标记暂不作为异常成立的强制条件。

研究页从新版本上线后的数据开始建样本，不回填旧异常。只有 GeckoTerminal 返回
`launchpad_details` 的 Token 会进入研究；同一 Token、同一规则版本只记录首次异常。
默认在首次异常30秒后寻找第一条可执行入场快照，从实际入场起跟踪5分钟。可执行性
使用100 USD名义本金、双边30 bps费用和常数乘积池深度估算，单边价格冲击不得超过
5%。launchpad 返回 `migrated_destination_pool_address` 后，研究会持续请求目标池，
并只使用迁移目标池完成后续入场或退出。以上口径可通过 `.env` 中的
`MEME_RESEARCH_*` 参数调整；成本模型不包含 Token 买卖税，页面会显式标记为估算值。

旧版原始快照应在新版本部署并运行一轮后清理。命令先预览，必须提供带时区的切换时间；
加 `--confirm` 才会删除该时间前的快照、关联旧异常和旧 Episode，且不会删除当前 Pair
状态：

```bash
uv run --env-file .env python manage.py purge_meme_snapshot_history \
  --before 2026-08-31T23:00:00+08:00
uv run --env-file .env python manage.py purge_meme_snapshot_history \
  --before 2026-08-31T23:00:00+08:00 --confirm
```

GeckoTerminal 免费 API 限制为每页20个新池、最多查询10页，且有公开接口频率限制。
因此启动后的持续发现是 MVP 的可靠路径；首次启动最多回看最新200个池，不能保证
枚举 BSC 完整24小时历史。`MEME_MONITOR_MAX_TRACKED_PAIRS` 默认也设为200，以便在
免费额度内完成每轮批量行情刷新。

## ETHUSDT 分钟盘口观察

微观结构页通过一个常驻采集进程同时接入 Binance USD-M Futures 的 1分钟 Kline
和 Top20 partial depth。Kline 提供真实成交价 OHLC、成交额和 taker buy quote
volume；主动卖出额由总成交额减主动买入额得到，Delta 为两者之差。Top20 盘口在
内存中实时更新，每秒抽样一次买卖深度和 spread。

数据库只保留可以直接展示的 `MarketMinute` 分钟事实，不保存逐笔成交或每秒盘口
历史。每分钟包含价格 OHLC、主动买卖额、Delta、Top20 买卖深度开/收/均值、
Top5盘口失衡均值/收盘值、Spread 均值/P95 和盘口抽样覆盖率。迁移
`microstructure.0004` 会删除旧的
`OrderBookSnapshot` 与 `OrderBookFiveMinuteSummary` 表及其数据。

先执行迁移。可以从侧边栏进入“微观结构 → 盘口采集”，在页面启动或停止采集；
也可以使用常驻命令：

```bash
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py collect_orderbook
```

页面地址：<http://127.0.0.1:8001/microstructure/>。页面展示最近2小时的 1分钟
K线、主动成交与 Delta、盘口深度与价差，点击任意分钟会联动右侧事实卡和所属
5分钟观察组；顶部“采集记录”可在当前任务和历史任务之间切换。页面默认跟随
最新分钟，主动选择历史分钟后会保持该选择；页面数据每60秒刷新一次，采集端仍
持续实时处理。

页面启动的采集是当前本地单机使用的独立子进程；关闭浏览器不会停止采集，应在
页面点击“停止采集”。
若已经从终端运行采集命令，页面会拒绝重复启动。

采集优先接收 WebSocket 1m Kline；同时每5秒从官方 REST Kline 拉取最近两根作为
容灾与分钟收盘校准，因此网络环境只放行 depth WebSocket 时，价格和主动成交仍会
持续更新。相同分钟采用幂等覆盖，重连不会重复累加成交额。按 `Ctrl+C` 可停止命令。

预测研究页：<http://127.0.0.1:8001/microstructure/research/>。当前在同一联合视图中
比较主动成交失衡、成交强度、Top20盘口深度快速减少、Spread扩大、Top5盘口失衡、
成交与价格背离和未来5分钟收益的关系；成交强度
定义为当前1分钟成交额相对前60个连续完整分钟成交额中位数的倍数，盘口深度减少
定义为分钟首尾Top20买卖总深度的下降比例；Spread扩大定义为当前分钟
`spread_bps_p95` 相对前60个连续有效分钟中位数的倍数；Top5盘口失衡使用分钟内
有效快照失衡值的均值；成交与价格背离仅保留主动成交方向与当分钟价格方向相反的
分钟，并以成交失衡乘当分钟绝对涨跌幅保留背离方向。三项盘口指标均要求盘口覆盖率
不低于80%。未来收益
只在当前分钟至5分钟后六根K线严格连续且全部收盘时生成。统一调度器每天
00:30（Asia/Shanghai）先增量补充研究标签，再为所有配置币种生成研究快照；
研究页读取最近一次成功快照，不在页面请求中扫描全部历史。研究按时间前70%发现、
后30%验证，并使用发现集十分位边界统计两段的样本数、平均未来收益和上涨比例。
研究快照不是正式异常信号。当前状态、六项候选指标和后续计划见
[微观结构预测研究路线图](docs/microstructure-research-roadmap.md)。
迁移后可手工生成标签和所有研究快照；定时任务也会执行同一流程：

```bash
uv run --env-file .env python manage.py generate_research_snapshots
```

可通过环境变量覆盖以下采集参数：

```dotenv
MICROSTRUCTURE_SYMBOL=ETHUSDT
MICROSTRUCTURE_WS_BASE_URL=wss://fstream.binance.com/public/ws
MICROSTRUCTURE_WS_UPDATE_SPEED=500ms
MICROSTRUCTURE_SAMPLE_INTERVAL_SECONDS=1
MICROSTRUCTURE_KLINE_POLL_SECONDS=5
MICROSTRUCTURE_RECONNECT_INITIAL_SECONDS=1
MICROSTRUCTURE_RECONNECT_MAX_SECONDS=30
MICROSTRUCTURE_WS_OPEN_TIMEOUT_SECONDS=10
# 未设置时继承 SOURCE_PROXY_URL；显式留空可强制直连
# MICROSTRUCTURE_WS_PROXY_URL=
```

当前版本的数据直接写入 PostgreSQL，没有 WAL、Parquet 或历史缺口回补；覆盖率
明确反映每分钟成功获得的盘口秒级样本。Top20 深度下降只能说明挂单量变化，不能
单独解释为成交或撤单，因此页面只呈现事实，不给出方向结论。

## 开仓分析助手 MVP

新菜单“开仓分析助手”：<http://127.0.0.1:8001/trading-assistant/>。
独立应用 `apps/trading_assistant` 使用 LangChain `create_agent`、DeepSeek 和
LangGraph PostgreSQL checkpoint。第一版支持 ETH/ZEC 盘口分析、多空与观望比较、
连续追问、指定参考入场价、候选止盈止损区间、成本后收益风险比与对话历史。
只分析 `MarketMinute` 中已收盘且在截止时间之前的数据，不读取未来收益标签。

初始化及运行（Web 与助手 worker 是两个进程）：

```bash
uv sync
uv run python manage.py migrate trading_assistant
uv run python manage.py runserver 8001
# 在另一个终端运行；关闭网页不会停止分析。
uv run python manage.py run_trading_assistant
```

worker 自动使用当前 `.env` 数据库连接，并在独立 `trading_assistant_agent` schema
初始化框架 checkpoint 表，首次运行账号需具备创建 schema 的权限。只需要运行一个
worker，数据库 advisory lock 防止重复启动。`--once` 仅处理一轮。
也可以在助手页面的离线提示中点击“启动分析服务”，无需另外打开终端。
按钮使用 Web 服务当前的 Python 环境及环境变量启动独立进程，页面自动检查就绪状态；
关闭网页不会停止它。重复点击不会重复启动，启动日志保存在 `.local_trading_assistant.log`。
按钮会自动处理符合条件的失效运行锁：仅检查当前数据库、当前账号持有的助手专用锁，
要求心跳失联及连接空闲均超过 6 分钟（分析超时更长时，等待时间为超时加 60 秒）。
清理前再次核对连接身份、空闲状态和心跳，只终止对应的旧助手连接；活跃查询、
刚启动或身份无法确认的连接会受到保护。数据库不可达、迁移缺失及权限不足会显示具体提示，
不会自动修改数据库结构或重启远端主机。心跳使用数据库时间，直接连接显式设置 UTC，
避免不同主机的时钟或会话时区影响判断；后台定期检查运行锁连接并启用 TCP 保活。
该按钮用于现有本地单用户运行方式，启动前仍需完成数据库连接和迁移。
模型默认沿用 `NEWS_AI_API_KEY / NEWS_AI_BASE_URL / NEWS_AI_MODEL`，支持独立的
`TRADING_ASSISTANT_*` 覆盖。DeepSeek 代理沿用“采集 → 来源与网络”的 DeepSeek 配置。
当前 DeepSeek 接入使用非思考模式和工具形式的结构化报告。没有启用 LangSmith 云追踪。

每轮任务先保存，再由 worker 领取。页面轮询显示状态，刷新或离开不会丢失任务。
同一会话仅允许一轮进行中，重复请求编号不会重复提交。worker 重启后恢复未完成
checkpoint；模型与工具调用有上限，单轮默认超时 300 秒。主动停止 worker 时，
正在执行的一轮重新排队；失败轮保留问题与已生成的工具记录，页面可重新提问。

每个会话固定币种；顶部可选择 15/30/60/120/240 分钟的分析情景，默认 60 分钟。
第一轮保存最近 24 小时的有界数据快照，后续勾选“更新行情”生成新快照，取消勾选
沿用最近成功报告的快照并限定为历史解释。模型携带最近六轮完整成功对话，以及
最近讨论的候选价格方案；完整聊天历史仍长期存储，旧消息可分页查看。
原始输入上下文、提示词文本/版本/hash、快照、工具参数/结果和最终报告都单独保存。

价格计划为明确规则计算的候选情景：用 5 分钟（持有不超过 60 分钟）或 15 分钟
聚合 K 线计算 14 个 True Range 的简单均值，结合最近 60 分钟局部高低点与波动
缓冲；目标超出历史区间时显式标注 ATR 外推。所选周期不是该时限内触价的预测。
示例成本按每边手续费 4 bps、滑点 2 bps 估算，未计资金费；不是用户实际费率。
显示区间为近似值，未按交易所 tick size 对齐，不是自动下单接口。
价格/主动成交覆盖不足、盘口采样不足、快照陈旧或 ATR 窗口有缺口时，限制结论或
拒绝生成价格方案。当前没有经过统一交易规则验证的胜率，界面固定显示“暂无可靠估计”。

提示词位于 `apps/trading_assistant/prompts/v1.md`；新增例如 `v2.md` 后设置
`TRADING_ASSISTANT_PROMPT_VERSION=v2` 并重启 worker。旧报告保留原始提示词与输入，
不会因文件修改而覆盖。公式在 `data.py`，工具入口在 `tools.py`，输出结构在
`schemas.py`。增加公式时同步调整 `CALCULATION_VERSION` 与对应测试。

第一版沿用现有本地单用户系统的访问方式，会话列表在该系统内共享；没有增加多用户
隔离或自动下单。若未来提供多人或公网访问，需要先接入认证与会话归属校验。

隔离测试使用 SQLite，不连接远端库创建或删除测试数据库：

```bash
uv run python manage.py test apps.trading_assistant apps.core --settings=config.test_settings
```

## 检查

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```
