[根目录](../CLAUDE.md) > **backend**

# backend — CLAUDE.md

> 变更记录 (Changelog)
> - 2026-04-18 20:35:46 — 初次生成

---

## 模块职责

FastAPI 后端，提供全部 REST API，封装 AI 写作业务逻辑（多层写作流水线、RAG 检索、情绪曲线分析、一致性检查等）与数据持久化（SQLAlchemy 异步 ORM）。

---

## 入口与启动

- 主入口：`app/main.py` — 装配路由、CORS、生命周期钩子（init_db + 提示词缓存预热）
- 运行命令：`uvicorn app.main:app --reload --port 8000`
- 健康检查：`GET /health` 和 `GET /api/health`

---

## 目录结构

```
backend/
├── app/
│   ├── main.py                   # FastAPI 应用入口
│   ├── api/
│   │   └── routers/              # 所有路由定义
│   │       ├── __init__.py       # api_router 聚合
│   │       ├── auth.py           # 认证（注册/登录/OAuth）
│   │       ├── novels.py         # 小说项目 CRUD
│   │       ├── writer.py         # 章节生成（L1/L2/L3 写作流）
│   │       ├── admin.py          # 管理后台
│   │       ├── analytics.py      # 基础分析
│   │       ├── analytics_enhanced.py  # 增强分析（前缀 /enhanced）
│   │       ├── foreshadowing.py  # 伏笔追踪
│   │       ├── llm_config.py     # 用户级 LLM 配置
│   │       ├── optimizer.py      # 文本优化
│   │       ├── projects.py       # 项目内存管理
│   │       ├── review.py         # 章节评审
│   │       └── updates.py        # 更新日志
│   ├── core/
│   │   ├── config.py             # Settings（pydantic-settings，所有环境变量）
│   │   ├── dependencies.py       # FastAPI 依赖（get_current_user 等）
│   │   └── security.py           # JWT 创建/解析，bcrypt 密码哈希
│   ├── db/
│   │   ├── session.py            # 异步引擎 + AsyncSessionLocal + get_session
│   │   ├── base.py               # DeclarativeBase
│   │   ├── init_db.py            # 建表 + 默认数据初始化
│   │   └── system_config_defaults.py  # 系统配置默认值
│   ├── models/                   # SQLAlchemy ORM 模型（见数据模型节）
│   ├── schemas/                  # Pydantic 请求/响应 Schema
│   ├── repositories/             # 数据访问层
│   ├── services/                 # 业务逻辑层（核心）
│   ├── utils/
│   │   ├── json_utils.py         # JSON 清洗工具（remove_think_tags 等）
│   │   ├── llm_tool.py           # LLMClient / ChatMessage 底层封装
│   │   └── emotion_analyzer.py   # 情绪分析工具函数
│   └── config/
│       └── celery_config.py      # Celery 配置（当前版本未启用）
└── requirements.txt
```

---

## 对外接口（API 路由一览）

| 前缀 | 路由文件 | 核心端点 |
|---|---|---|
| `POST /api/auth/register` | `auth.py` | 用户注册 |
| `POST /api/auth/login` | `auth.py` | 用户名密码登录，返回 JWT |
| `GET /api/auth/linuxdo/*` | `auth.py` | Linux.do OAuth 流程 |
| `GET/POST /api/novels` | `novels.py` | 小说项目列表 / 创建 |
| `POST /api/novels/{id}/converse` | `novels.py` | 概念阶段对话 |
| `POST /api/novels/{id}/blueprint` | `novels.py` | 生成蓝图 |
| `POST /api/writer/generate-outline` | `writer.py` | 生成章节大纲 |
| `POST /api/writer/generate` | `writer.py` | 基础章节生成 |
| `POST /api/writer/advanced-generate` | `writer.py` | 高级写作流水线（PipelineOrchestrator） |
| `POST /api/writer/evaluate` | `writer.py` | 章节 AI 评审 |
| `POST /api/writer/finalize` | `writer.py` | 章节定稿（向量入库） |
| `GET /api/admin/*` | `admin.py` | 用户管理、提示词、系统配置 |
| `GET /api/analytics/*` | `analytics.py` | 写作统计 |
| `GET /api/analytics/enhanced/*` | `analytics_enhanced.py` | 情绪曲线、故事弧等 |

认证：Bearer JWT，通过 `get_current_user` 依赖注入。

---

## 关键依赖与配置

```
fastapi==0.110.0
uvicorn[standard]==0.29.0
sqlalchemy==2.0.44
asyncmy==0.2.9          # MySQL 异步驱动
aiosqlite==0.21.0       # SQLite 异步驱动
alembic==1.13.1
pydantic==2.12.2
openai==2.3.0
libsql-client==0.3.1    # 向量库（RAG）
ollama==0.6.0           # 本地嵌入模型可选
langchain-text-splitters==0.3.11
redis==5.0.7            # 缓存（可选）
python-jose==3.3.0      # JWT
passlib[bcrypt]==1.7.4  # 密码哈希
```

---

## 数据模型

### 主要表

| 表名 | 模型类 | 说明 |
|---|---|---|
| `users` | `User` | 用户账号，含 is_admin / is_active |
| `novel_projects` | `NovelProject` | 小说项目元数据，UUID 主键 |
| `novel_conversations` | `NovelConversation` | 概念阶段对话历史 |
| `novel_blueprints` | `NovelBlueprint` | 蓝图（类型、简介、世界观等） |
| `blueprint_characters` | `BlueprintCharacter` | 蓝图角色信息 |
| `blueprint_relationships` | `BlueprintRelationship` | 角色关系 |
| `chapter_outlines` | `ChapterOutline` | 章节大纲（含导演脚本 JSON） |
| `chapters` | `Chapter` | 章节正文状态，指向选中版本 |
| `chapter_versions` | `ChapterVersion` | 章节生成的多个候选版本 |
| `chapter_evaluations` | `ChapterEvaluation` | 章节 AI 评分记录 |
| `llm_configs` | `LLMConfig` | 用户级 LLM 配置（可覆盖全局） |
| `prompts` | `Prompt` | 提示词库（由管理员维护） |
| `foreshadowings` | `Foreshadowing` | 伏笔条目 |
| `memory_layers` | `MemoryLayer` | 项目级记忆层 |
| `project_memories` | `ProjectMemory` | 项目内存快照 |

数据库支持 MySQL（asyncmy）和 SQLite（aiosqlite），通过 `DB_PROVIDER` 环境变量切换。迁移通过 alembic 管理。

---

## 核心服务层

| 服务文件 | 职责 |
|---|---|
| `pipeline_orchestrator.py` | 高级写作流水线入口，组装所有可选模块（RAG/一致性/预览/自评等） |
| `llm_service.py` | LLM 调用封装（流式 + 收集、配额控制、模型选择） |
| `novel_service.py` | 小说项目 CRUD 业务逻辑 |
| `blueprint_service.py` | 蓝图生成逻辑 |
| `chapter_context_service.py` | 章节上下文组装 |
| `vector_store_service.py` | libsql 向量存储（RAG 剧情片段 + 章节摘要检索） |
| `embedding_service.py` | 文本向量化（OpenAI / Ollama） |
| `consistency_service.py` | 角色/世界观一致性检查 |
| `foreshadowing_service.py` | 伏笔埋设与追踪 |
| `emotion_curve_service.py` | 情绪曲线分析 |
| `self_critique_service.py` | 章节自评 |
| `reader_simulator_service.py` | 读者视角模拟 |
| `prompt_service.py` | 提示词管理（数据库读写 + 缓存预热） |
| `auth_service.py` | 用户注册/登录/OAuth 逻辑 |

---

## 测试与质量

- 唯一测试文件：`services/test_phase4_integration.py`（探索性集成测试，非正式）
- 无 pytest 配置，无 CI
- 缺口：单元测试覆盖率 0%，建议优先为 `pipeline_orchestrator`、`llm_service`、`novel_service` 补充 pytest

---

## 常见问题 (FAQ)

**Q: 如何切换数据库？**
设置 `DB_PROVIDER=sqlite` 或 `DB_PROVIDER=mysql`，并配置对应连接参数，重启即可。

**Q: RAG 不工作？**
确认已设置 `VECTOR_DB_URL`（本地：`file:./storage/rag_vectors.db`）且已调用章节定稿接口将章节写入向量库。

**Q: 如何新增提示词？**
通过管理后台 `/admin` → 提示词管理，或直接在数据库 `prompts` 表插入。

---

## 相关文件清单

- `/Users/licocon/github/arboris-novel/backend/app/main.py`
- `/Users/licocon/github/arboris-novel/backend/app/core/config.py`
- `/Users/licocon/github/arboris-novel/backend/app/api/routers/__init__.py`
- `/Users/licocon/github/arboris-novel/backend/app/services/pipeline_orchestrator.py`
- `/Users/licocon/github/arboris-novel/backend/app/services/llm_service.py`
- `/Users/licocon/github/arboris-novel/backend/app/models/novel.py`
- `/Users/licocon/github/arboris-novel/backend/requirements.txt`
