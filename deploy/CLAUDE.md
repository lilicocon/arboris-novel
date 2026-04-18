[根目录](../CLAUDE.md) > **deploy**

# deploy — CLAUDE.md

> 变更记录 (Changelog)
> - 2026-04-18 20:35:46 — 初次生成

---

## 模块职责

包含生产部署所需的全部配置：多阶段 Dockerfile（前端构建 + 后端 + nginx 合并为单镜像）、docker-compose（SQLite 默认 / MySQL 可选）、nginx 反向代理、supervisor 进程管理、entrypoint 脚本与环境变量模板。

---

## 入口与启动

```bash
cd deploy
cp .env.example .env        # 按需修改
docker compose up -d        # 默认 SQLite，端口 8088
# 或使用 MySQL
DB_PROVIDER=mysql docker compose --profile mysql up -d
```

---

## 文件清单

| 文件 | 说明 |
|---|---|
| `Dockerfile` | 两阶段构建：node:20-slim 构建前端静态文件；python:3.11-slim 运行后端 + nginx |
| `docker-compose.yml` | 应用服务（必选）+ MySQL 服务（profile: mysql，可选）|
| `.env.example` | 所有环境变量说明与默认值模板 |
| `nginx.conf` | nginx 反向代理配置，静态文件服务 + `/api` 转发至 uvicorn :8000 |
| `supervisord.conf` | supervisor 配置，同时管理 nginx 和 uvicorn 进程 |
| `docker-entrypoint.sh` | 容器启动脚本，处理目录权限后移交 supervisor |

---

## 镜像架构

```
Container (python:3.11-slim)
├── /usr/share/nginx/html/    # 前端 dist（from node:20-slim builder）
├── /app/                     # 后端代码
│   └── storage/              # 数据库 / 向量库持久化目录（挂载 volume）
├── nginx :80                 # 静态文件 + 反向代理
└── uvicorn :8000             # FastAPI 后端
```

supervisor 以 root 启动后降权，nginx 和 uvicorn 分别以各自用户运行。

---

## 数据持久化

| 数据 | 默认路径 | Volume |
|---|---|---|
| SQLite 数据库 | `/app/storage/arboris.db` | `sqlite-data` |
| 向量库 | `/app/storage/rag_vectors.db` | 同上 |
| MySQL 数据（若启用） | MySQL 容器内 | `mysql-data` |

若要将 SQLite 存储映射到宿主机目录，设置 `SQLITE_STORAGE_SOURCE=./storage`。

---

## 关键环境变量

参见 `.env.example` 中的完整注释说明。必须修改项：

- `SECRET_KEY` — JWT 密钥，生产环境用 `openssl rand -hex 32` 生成
- `ADMIN_DEFAULT_PASSWORD` — 默认管理员密码，首次启动后立即修改
- `OPENAI_API_KEY` — LLM 服务密钥
- `MYSQL_PASSWORD` / `MYSQL_ROOT_PASSWORD`（MySQL 模式）

---

## 健康检查

docker-compose 对 `arboris-app` 服务配置了健康检查：

```
curl -f http://127.0.0.1:8000/api/health
interval: 30s / timeout: 10s / retries: 3 / start_period: 90s
```

---

## 相关文件清单

- `/Users/licocon/github/arboris-novel/deploy/Dockerfile`
- `/Users/licocon/github/arboris-novel/deploy/docker-compose.yml`
- `/Users/licocon/github/arboris-novel/deploy/.env.example`
