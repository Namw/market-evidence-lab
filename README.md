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

## ETHUSDT 分钟盘口观察

微观结构页通过一个常驻采集进程同时接入 Binance USD-M Futures 的 1分钟 Kline
和 Top20 partial depth。Kline 提供真实成交价 OHLC、成交额和 taker buy quote
volume；主动卖出额由总成交额减主动买入额得到，Delta 为两者之差。Top20 盘口在
内存中实时更新，每秒抽样一次买卖深度和 spread。

数据库只保留可以直接展示的 `MarketMinute` 分钟事实，不保存逐笔成交或每秒盘口
历史。每分钟包含价格 OHLC、主动买卖额、Delta、Top20 买卖深度开/收/均值、
Spread 均值/P95 和盘口抽样覆盖率。迁移 `microstructure.0004` 会删除旧的
`OrderBookSnapshot` 与 `OrderBookFiveMinuteSummary` 表及其数据。

先执行迁移。可以从侧边栏进入“微观结构 → 盘口采集”，在页面启动或停止采集；
也可以使用常驻命令：

```bash
uv run --env-file .env python manage.py migrate
uv run --env-file .env python manage.py collect_orderbook
```

页面地址：<http://127.0.0.1:8001/microstructure/>。页面展示最近2小时的 1分钟
K线、主动成交与 Delta、盘口深度与价差，点击任意分钟会联动右侧事实卡和所属
5分钟观察组；页面数据每60秒刷新一次，采集端仍持续实时处理。

页面启动的采集是当前本地单机使用的独立子进程；关闭浏览器不会停止采集，应在
页面点击“停止采集”。
若已经从终端运行采集命令，页面会拒绝重复启动。

采集优先接收 WebSocket 1m Kline；同时每5秒从官方 REST Kline 拉取最近两根作为
容灾与分钟收盘校准，因此网络环境只放行 depth WebSocket 时，价格和主动成交仍会
持续更新。相同分钟采用幂等覆盖，重连不会重复累加成交额。按 `Ctrl+C` 可停止命令。

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

## 检查

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```
