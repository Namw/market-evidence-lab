# Market Evidence Lab

Market Evidence Lab 是一个面向市场证据工作流的 Django 单体项目。当前完成第 2 阶段“ETHUSDT K线采集闭环”：从页面提交 UTC 日期范围，经统一采集服务读取 Binance USD-M Futures 公开K线并写入 PostgreSQL，同时保留逐周期运行记录。

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

当前页面同步执行采集，不包含后台任务或自动调度。

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

## 检查与测试

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```

自动化测试使用 mock HTTP 响应，不会真实访问 Binance。

## 当前未实现

当前没有实现自动调度、后台任务、实时 WebSocket、数据质量检查、缺口或连续性结论、技术指标、K线图、数据分析、巡检、研究案例、AI报告、人工反馈、登录权限或 Django Admin 页面。没有采集 1m、OI、Funding、订单簿、成交明细或新闻。
