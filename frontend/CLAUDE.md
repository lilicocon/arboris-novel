[根目录](../CLAUDE.md) > **frontend**

# frontend — CLAUDE.md

> 变更记录 (Changelog)
> - 2026-04-18 20:35:46 — 初次生成

---

## 模块职责

Vite + Vue 3 + TypeScript 前端，实现写作台、大纲编辑、角色 / 派系 / 关系管理、章节生成与多版本对比等完整写作辅助交互界面，通过 Naive UI 组件库与 TailwindCSS 4 构建 UI。

---

## 入口与启动

- 主入口：`src/main.ts` — 注册 Vue 应用、Pinia、vue-router
- 开发服务器：`npm run dev`（Vite dev server，默认 :5173）
- 构建：`npm run build`（类型检查 + vite build，产出 `dist/`）
- 类型检查：`npm run type-check`（vue-tsc）

---

## 目录结构

```
frontend/
├── src/
│   ├── main.ts                   # 应用入口，注册插件
│   ├── App.vue                   # 根组件
│   ├── router/
│   │   └── index.ts              # 路由表 + 导航守卫（权限控制）
│   ├── stores/
│   │   ├── auth.ts               # 用户认证状态（Pinia）
│   │   └── novel.ts              # 小说项目状态（Pinia）
│   ├── api/
│   │   ├── novel.ts              # 小说 / 章节 / 蓝图 API 客户端
│   │   ├── admin.ts              # 管理后台 API 客户端
│   │   ├── llm.ts                # LLM 配置 API 客户端
│   │   └── updates.ts            # 更新日志 API 客户端
│   ├── views/                    # 页面级视图（路由直接挂载）
│   ├── components/               # 通用 + 业务组件
│   │   ├── admin/                # 管理后台组件
│   │   ├── novel-detail/         # 小说详情各分区组件
│   │   ├── writing-desk/         # 写作台组件集合
│   │   │   └── workspace/        # 写作台工作区子组件
│   │   └── shared/               # 跨页面复用的 Shell 组件
│   ├── composables/
│   │   └── useAlert.ts           # 全局 Alert 钩子
│   └── utils/
│       └── date.ts               # 日期格式工具
├── package.json
└── vite.config.ts
```

---

## 路由与页面

| 路径 | 组件 | 权限 | 说明 |
|---|---|---|---|
| `/` | `WorkspaceEntry` | 登录 | 首页，项目选择入口 |
| `/workspace` | `NovelWorkspace` | 登录 | 项目列表 / 管理 |
| `/inspiration` | `InspirationMode` | 登录 | 灵感模式 |
| `/detail/:id` | `NovelDetail` | 登录 | 小说详情（设定/大纲/角色等） |
| `/novel/:id` | `WritingDesk` | 登录 | 写作台（章节生成与编辑） |
| `/login` | `Login` | 公开 | 登录页 |
| `/register` | `Register` | 公开 | 注册页 |
| `/admin` | `AdminView` | 登录 + is_admin | 管理后台 |
| `/admin/novel/:id` | `AdminNovelDetail` | 登录 + is_admin | 管理员查看小说详情 |
| `/settings` | `SettingsView` | 登录 | 用户设置（LLM 配置等） |

导航守卫处理：未登录跳 `/login`；非管理员访问 admin 路由跳 `/`；管理员首次登录强制跳密码修改页。

---

## 状态管理（Pinia）

| Store | 文件 | 核心状态 |
|---|---|---|
| `auth` | `stores/auth.ts` | `token`、`user`、`isAuthenticated`、`mustChangePassword` |
| `novel` | `stores/novel.ts` | `projects`、`currentProject`、`isLoading`、`pendingChapterEdits` |

---

## API 客户端

HTTP 请求统一使用原生 `fetch` 封装的 `request()` 函数（`src/api/novel.ts`）：
- 自动注入 `Authorization: Bearer <token>`
- 收到 401 自动清除 token 并跳转 `/login`
- 生产环境使用相对路径，开发环境代理至 `http://127.0.0.1:8000`

---

## 关键依赖

| 包 | 用途 |
|---|---|
| `vue@3.5` | 核心框架 |
| `vue-router@4` | 路由 |
| `pinia@3` | 状态管理 |
| `naive-ui@2.39` | UI 组件库 |
| `tailwindcss@4` | 原子化 CSS |
| `chart.js@4` | 情绪曲线图表 |
| `marked@16` | Markdown 渲染 |
| `@headlessui/vue` | 无样式可访问性组件 |
| `vite@7` | 构建工具 |
| `vue-tsc` | TypeScript 类型检查 |

---

## 测试与质量

- 无单元测试（无 vitest 配置）
- 代码格式化：`npm run format`（prettier）
- 类型检查：`npm run type-check`（vue-tsc）
- 缺口：建议为 stores 和 API 客户端函数补充 vitest 单元测试

---

## 常见问题 (FAQ)

**Q: 开发时 API 请求跨域？**
`API_BASE_URL` 在开发模式下为 `http://127.0.0.1:8000`，确保后端已启动并开启 CORS（已默认允许所有来源）。

**Q: 如何新增一个页面？**
1. 在 `src/views/` 创建 `.vue` 文件
2. 在 `src/router/index.ts` 添加路由记录
3. 按需在对应 store 或 API 文件扩展数据层

---

## 相关文件清单

- `/Users/licocon/github/arboris-novel/frontend/src/main.ts`
- `/Users/licocon/github/arboris-novel/frontend/src/router/index.ts`
- `/Users/licocon/github/arboris-novel/frontend/src/api/novel.ts`
- `/Users/licocon/github/arboris-novel/frontend/src/stores/auth.ts`
- `/Users/licocon/github/arboris-novel/frontend/src/stores/novel.ts`
- `/Users/licocon/github/arboris-novel/frontend/package.json`
- `/Users/licocon/github/arboris-novel/frontend/vite.config.ts`
