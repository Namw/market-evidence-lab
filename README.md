# Market Evidence Lab

Market Evidence Lab 是一个面向市场证据工作流的新项目。本仓库当前仅交付第 1 阶段“项目骨架”，用于建立可运行、可测试的 Django 与 PostgreSQL 基础环境。

## 当前阶段已完成

- Python 3.12 与 `uv` 依赖管理
- Django 5.2 单体项目配置
- 唯一的 `apps/core` 核心应用
- 可访问的总览页面和禁用状态的后续功能入口
- PostgreSQL 16 本地 Docker Compose 环境
- 首页基础测试

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker 与 Docker Compose

## 本地启动

复制本地环境变量示例：

```bash
cp .env.example .env
```

`.env` 已被 Git 忽略。示例值仅适用于本地开发，部署时必须替换密钥和数据库凭据。

启动 PostgreSQL 16：

```bash
docker compose up -d
docker compose ps
```

安装锁定的依赖：

```bash
uv sync
```

执行数据库迁移：

```bash
uv run --env-file .env python manage.py migrate
```

启动 Django：

```bash
uv run --env-file .env python manage.py runserver
```

打开 <http://127.0.0.1:8000/> 查看总览页面。

## 检查与测试

```bash
uv run --env-file .env python manage.py check
uv run --env-file .env python manage.py test
uv run --env-file .env python manage.py makemigrations --check --dry-run
```

## 尚未实现

本阶段没有实现采集、分析、巡检、调度、运行记录、数据质量、研究案例、AI 报告或人工反馈等业务功能，也没有创建相应的业务应用和数据模型。导航中的相关入口仅显示流程与“待建设”状态。
# market-evidence-lab
