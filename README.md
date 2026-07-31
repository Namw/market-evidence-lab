# Market Evidence Lab

Market Evidence Lab 是一个面向市场证据工作流的 Django 单体项目。当前完成第 3 阶段“K线数据质量巡检闭环”：在既有 ETHUSDT K线采集能力之上，对 PostgreSQL 中的 1d、1h K线执行手工质量巡检并保存逐周期结果与异常详情。

## 当前实现范围

- Python 3.12、Django 5.2、PostgreSQL 16 与 `uv` 依赖管理
- Binance USD-M Futures 公开接口 `/fapi/v1/klines`
- 固定品种 ETHUSDT
- 固定周期 1d、1h
- 已闭合K线的分页采集和有限重试
- 精确 Decimal 数值保存，不经过 `float`
- 幂等写入：新数据插入、变化数据更新、相同数据跳过
- 每个周期独立的采集运行记录
- `/collection/` 手工采集、数据概况与最近运行历史页面
- `/inspection/` 手工巡检、最近运行与指定运行详情页面
- 缺失、重复、时间未对齐、OHLC、负数值与 close_time 检查
- 连续缺失时间压缩和最多 200 项异常详情保护

当前采集和巡检页面均同步执行，不包含后台任务或自动调度。巡检只报告问题，不会自动修复或补采。

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker 与 Docker Compose

## 本地环境

复制环境变量示例：

```bash
cp .env.example .env
```

`.env` 已被 Git 忽略。示例中的密钥和数据库密码只适用于本地开发。`BINANCE_FUTURES_BASE_URL` 默认指向 Binance USD-M Futures 公开 API，可在受控测试环境中覆盖。

启动 PostgreSQL 16 并检查状态：

```bash
docker compose up -d
docker compose ps
```

默认将容器内 PostgreSQL `5432` 映射到宿主机 `55432`，可以通过 `POSTGRES_PORT` 修改。

安装锁定依赖：

```bash
uv sync
```

执行迁移：

```bash
uv run --env-file .env python manage.py migrate
```

启动 Django：

```bash
uv run --env-file .env python manage.py runserver
```

访问：

- 总览：<http://127.0.0.1:8000/>
- 采集：<http://127.0.0.1:8000/collection/>
- 巡检：<http://127.0.0.1:8000/inspection/>

## 手工采集

在采集页面填写开始日期、结束日期，并选择 1d、1h 中的至少一个周期后提交。

- 日期统一按 UTC 解释。
- 开始日期的 `00:00` 包含在采集范围内。
- 结束日期的 `00:00` 不包含在采集范围内。
- 开始日期必须早于结束日期，且不能是未来日期。
- 单次范围最长 366 天。
- 每个周期独立采集和记录；一个周期失败不会阻止另一个周期继续。
- 提交后 API 分页、数据库写入都在当前 HTTP 请求内同步执行，较大范围需要等待。

运行记录中的统计口径：

- `request_count`：包含重试在内的真实 HTTP 请求次数。
- `received_count`：Binance 成功响应返回的原始K线条数。
- `inserted_count`：新增数据库记录数。
- `updated_count`：已有记录字段实际变化的数量。
- `skipped_count`：范围外、未闭合、分页重复或数据库中完全相同的数据数量。

## 手工巡检

在巡检页面填写开始日期、结束日期，并选择 1d、1h 中的至少一个周期后提交。

- 范围按 `[开始日期 00:00, 结束日期 00:00)` UTC 解释。
- 开始日期必须早于结束日期，单次范围最长 366 天。
- 结束日期不得超过当前 UTC 日期 00:00，确保范围内K线已经闭合。
- 每个周期独立巡检；一个周期执行失败不会阻止另一个周期。
- 执行状态表示巡检程序是否正常完成，质量状态表示数据是否存在问题。
- 发现数据问题时执行状态仍为成功，质量状态为“发现问题”。
- 通过 `/inspection/?run=<运行ID>` 查看指定运行详情。

巡检规则包括：

- 根据 1h UTC 整点或 1d UTC 00:00 生成预期 open_time。
- 对比实际数据并压缩连续缺失时间区间。
- 按市场逻辑键报告重复 open_time。
- 检查 open_time 周期对齐。
- 检查 OHLC 正数与高低价关系。
- 检查成交量、成交笔数和主动买入量不得为负。
- 检查 close_time 等于下一个周期边界减 1 毫秒。

异常统计始终保持完整；保存的异常详情全局最多 200 项，超过时标记为已截断。

## 检查与测试

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```

自动化测试使用 mock HTTP 响应，不会真实访问 Binance。

## 当前未实现

当前没有实现自动调度、后台任务、实时 WebSocket、自动补采或修复、技术指标、K线图、行情分析、研究案例、AI报告、人工反馈、登录权限或 Django Admin 页面。没有采集 1m、OI、Funding、订单簿、成交明细或新闻。
