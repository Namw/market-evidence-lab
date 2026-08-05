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

## 检查

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```
