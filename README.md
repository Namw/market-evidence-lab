# Market Evidence Lab

Market Evidence Lab 是一个聚焦 ETHUSDT 市场数据与新闻事件观察的 Django 项目。

## 当前范围

项目只保留三组产品能力：

1. 采集与调度
   - Binance USD-M Futures ETHUSDT 1d / 1h / 5m K线采集。
   - 1h / 5m OI 与实际 Funding 结算数据采集。
   - 多新闻源采集。
   - 采集质量检查、调度配置和运行记录。
2. 新闻观察
   - 新闻原始数据查看与手工采集。
   - ETH 方向分类。
   - 客观事实提取与校验。
   - 多来源新闻归并为暂定事件库。
3. 行情数据观察
   - 联动展示日 K、小时 K、OI 与 Funding；点击1h K线查看对应的5m K线与5m OI。

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

## 检查

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```
