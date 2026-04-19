# Arboris-Novel 项目审计与修复方案

> 日期：2026-04-19
> 范围：后端生成链路、JSON 协议、工作台交互、移动端体验、GitHub Issues 快照
> 结论用途：作为后续修复工作的统一执行文档

---

## 0. 当前执行进度（2026-04-19）

### 已完成

1. `usage_metrics` 已从主事务里拆开，不再在 LLM 主请求 session 上直接 `commit()`。
2. SQLite 会话已补 `WAL / busy_timeout / synchronous=NORMAL`。
3. SQLite 环境下章节多版本生成已自动降成串行，避免并行写库打锁。
4. 独立评审接口失败时不再直接向前端抛 500，而是落 `evaluation_failed` 状态并返回项目快照。
5. `evaluation` 路由输入已改成结构化 JSON，不再只喂纯正文。
6. 工作台已补“下一章”“生成下一章”“确认并生成下一章”入口，减少来回开侧边栏。
7. 大纲已支持章节级状态流：
   - `draft / approved / needs_regen`
   - 可单章标记
   - 可批量重抽 `needs_regen` 章节
8. 标准生成路由已改为委托 `PipelineOrchestrator`，不再继续维护两套生成实现。
9. 关键 JSON 生成链路已接入 `StructuredLLMService`：
   - 章节导演脚本
   - 大纲生成 / 扩写 / 重抽
   - AIReviewService
   - 独立评审接口
10. 移动端工作台已补底部主操作条：
   - 目录
   - 上一章 / 下一章
   - 当前章生成 / 重生成主按钮
11. 剩余关键 JSON 服务也已统一到结构化解析入口：
    - `preview_generation`
    - `memory_layer`
    - `reader_simulator`
    - `chapter_review`
    - `constitution`
    - `six_dimension_review`
    - `foreshadowing_tracker`
    - `self_critique`
12. 详情页章节大纲已支持手动多选并批量重抽。
13. 移动端头部已改成“更多菜单”，把详情/导出/退出登录从主按钮区挪开。

### 仍未完成

1. 工作台移动端视觉层面仍可继续打磨，但核心操作路径已经打通。

---

## 1. 审计结论

### 1.1 高优先级问题

1. **SQLite 下并发生成会把会话打进回滚状态，直接导致 500。**
   - 触发路径已经从日志坐实：`LLMService._stream_and_collect()` 在请求主事务里调用 `UsageService.increment()`，而 `UsageService.increment()` 直接 `commit()`。
   - 章节并行生成、AI 评审、扩写、向量写入同时发生时，SQLite 很容易报 `database is locked`，随后主 session 进入 `PendingRollbackError`。
   - 这会直接表现成两类用户可见故障：
     - **连续生成**：界面提示“最近失败：请求失败，状态码 500”
     - **AI 评审**：评审阶段报 500，或者在高级生成里被记录为 `AI 评审失败`
   - 相关文件：
     - `backend/app/services/llm_service.py`
     - `backend/app/services/usage_service.py`
     - `backend/app/services/pipeline_orchestrator.py`
     - `backend/app/db/session.py`

2. **评审接口与评审 prompt 的输入契约仍然不一致。**
   - `writer.py` 的 `/novels/{project_id}/chapters/evaluate` 仍然只把章节正文直接丢给 `evaluation` prompt。
   - 这和 Issue #35 提到的问题一致：后端没有构造包含蓝图、章节大纲、历史摘要等上下文的结构化输入。
   - 结果是评审质量不稳定，而且 prompt 演化空间被锁死。
   - 相关文件：
     - `backend/app/api/routers/writer.py`
     - `backend/prompts/evaluation.md`

3. **结构化输出链路不统一，非法 JSON 风险仍然存在。**
   - 项目里有两套混用方式：
     - 一部分走 `get_llm_response(..., response_format="json_object")`
     - 一部分走 `generate()` 或手动截 `{...}` 再 `json.loads()`
   - 所以“作者没有采用模型原生 JSON 输出”这句话不完全对，但“项目整体缺少统一的结构化输出层”是成立的。
   - 风险点主要在：
     - `backend/app/services/finalize_service.py`
     - `backend/app/services/constitution_service.py`
     - `backend/app/services/memory_layer_service.py`
     - `backend/app/services/preview_generation_service.py`
     - `backend/app/services/chapter_review_service.py`

4. **显式内容场景仍然会被主模型打空响应。**
   - 你给的日志里，`grok-4.20-auto` 多次返回 `finish_reason=stop` 但正文为空。
   - 这类失败目前只会被当成普通空响应处理，缺少“分级内容 → 专用模型/专用策略”的兜底。
   - 当前代码已经有 `content_rating` 基础链路，但如果部署环境没有配置 `MATURE_* / EXPLICIT_*` 模型，实际效果还是会落回默认模型。
   - 这也是“AI 评审经常 500”的第二个来源：评审 prompt 走的还是同一个 provider，同样会遇到空白返回。

### 1.2 中优先级问题

5. **工作台移动端交互确实不顺。**
   - 侧边栏在移动端仍然是固定宽度面板，主要生成操作藏在侧边栏里。
   - 用户需要“打开侧边栏 → 选章节 → 生成/连续生成”，路径长，单手操作不友好。
   - 相关文件：
     - `frontend/src/components/writing-desk/WDSidebar.vue`
     - `frontend/src/views/WritingDesk.vue`
     - `frontend/src/components/writing-desk/WDWorkspace.vue`
   - GitHub 对应反馈：
     - Issue #15 `蓝图导航的开始创作按钮跳转异常，创作页面不兼容手机端`

6. **确认版本后不会自动切到下一章，流转断裂。**
   - `confirmVersionSelection()` 最终只会调用当前章的 `selectVersion()`。
   - 连续生成能力是有的，但被藏在侧边栏的“连续生成”里，不是默认主路径。
   - 用户反馈“确认完一章后没有自动生成下一章”与代码一致。
   - 相关文件：
     - `frontend/src/views/WritingDesk.vue`

7. **章节大纲缺少“逐章确认/逐章重抽”的工作流。**
   - 当前已支持：
     - 批量生成
     - 补齐缺失
     - AI 扩写
     - 手动编辑
   - 但没有：
     - 每章“满意/锁定”
     - 对单章或选中章节重新生成
     - 锁定满意章节后只重抽不满意章节
   - 用户“只能一次又一次抽卡”的反馈基本属实。
   - 相关文件：
     - `backend/app/services/outline_generation_service.py`
     - `frontend/src/components/novel-detail/ChapterOutlineSection.vue`
     - `frontend/src/components/shared/NovelDetailShell.vue`

8. **标准生成路由和 orchestrator 仍然双轨并存。**
   - `/api/writer/advanced/generate` 走 `PipelineOrchestrator`
   - `/api/writer/novels/{project_id}/chapters/generate` 仍保留旧逻辑
   - 两条链路功能不对齐，修一个地方很容易漏另一个地方。
   - 相关文件：
     - `backend/app/api/routers/writer.py`

### 1.3 低优先级或已部分修复的问题

9. **手动编辑无效**：当前代码看起来已经补过。
   - 后端会更新 `selected_version` 或最新版本；
   - 前端也有 `pendingChapterEdits` 做覆盖保护。
   - 但没有自动化回归测试，建议补一条。
   - GitHub 对应：
     - Issue #21 `文章生成后手动编辑无效`

10. **编辑/新增章节大纲后导致已生成章节重新排序**：现有代码里没有直接看到这个问题仍在。
   - 后端序列化和前端展示基本都按 `chapter_number` 排序。
   - 这更像历史 bug，建议补回归测试后再判断是否关闭。
   - GitHub 对应：
     - Issue #25 `编辑或新增章节大纲后会导致已生成的章节重新排序`

---

## 2. 用户反馈核对

| 反馈 | 结论 | 说明 |
|---|---|---|
| 经常返回非法 JSON，可能没用模型原生 JSON 输出 | **部分属实** | 项目里一部分链路用了原生 JSON，一部分仍是手工解析，协议层没统一 |
| 手机端浮动面板和功能排布反人类 | **属实** | 生成入口和章节导航主要依赖侧边栏，移动端路径长 |
| 大纲每章约束太少，只能反复抽卡 | **属实** | 现在没有逐章确认/锁定/重抽机制 |
| 确认完一章后没有自动生成下一章 | **属实** | 默认流程不会自动跳到下一章，只有隐藏的“连续生成” |
| 生成按钮和退出登录很容易误触 | **部分属实** | 不是同一个组件里的同一按钮区，但移动端顶部和侧边栏操作路径确实拥挤 |
| 调整好大纲后，正文质量还可以 | **属实** | 当前生成质量更多依赖大纲质量和后续人工微调，而不是流程本身顺滑 |
| 连续生成会报 500 | **属实** | 当前已经从日志坐实，根因优先指向 SQLite 锁冲突和主事务污染 |
| AI 评审一直报 500 | **属实** | 至少有两类根因：上游空白响应、`usage_metrics` 更新引发的锁冲突 |

---

## 3. GitHub Issues 快照（2026-04-19）

### 3.1 仓库现状

- Open issues：26
- Closed issues：4

### 3.2 与当前审计直接相关的 issue

- #35 `AI 评审输入未按 evaluation 提示词构建 — 后端只传入章节文本而非包含蓝图/前序摘要的 JSON`
  - 结论：**仍然存在**
- #15 `蓝图导航的开始创作按钮跳转异常，创作页面不兼容手机端`
  - 结论：**移动端工作流问题仍存在**
- #8 `总是报 502 和 504 能不能优化一下`
  - 结论：**仍然有根因未清**
  - 当前至少能确认两种来源：上游 LLM 空响应、SQLite 锁冲突
- #31 / #20 `生成蓝图失败`
  - 结论：**仍需继续查**
  - 结构化输出不统一和上游空响应都可能触发
- #21 `文章生成后手动编辑无效`
  - 结论：**看代码像已修，但缺回归测试**
- #25 `编辑或新增章节大纲后会导致已生成的章节重新排序`
  - 结论：**看代码像已修，但缺回归测试**

### 3.3 其它 issue 分类

其它 open issue 主要分四类：

1. 登录/注册与在线体验
   - #37、#26
2. 本地运行 / Docker / 部署问题
   - #36、#23、#19、#11、#10、#6
3. 生成失败 / 通信失败 / 体验问题
   - #27、#9、#8
4. 产品功能建议
   - #28、#22、#13、#7、#5

这些问题这次没有逐条复现，但从 issue 标题和当前代码看，至少“部署文档不足”“体验流程不顺”“错误分类不清”三类都是真问题。

---

## 4. 修复方案

### Phase 0：稳定性止血

目标：先把 500、空响应、锁表这些会直接把用户流程打断的问题处理掉。

1. **把 usage 计数从主事务里拆出去。**
   - 禁止 `LLMService._stream_and_collect()` 在当前请求 session 上 `commit()`
   - 方案二选一：
     - 独立 `AsyncSessionLocal` 记账，失败只记日志
     - 直接改成内存/Redis 计数，批量落库
   - SQLite 下额外打开：
     - `WAL`
     - `busy_timeout`
2. **给 SQLite 并发场景加硬保护。**
   - SQLite 环境下默认禁用章节多版本并行
   - 或至少把并行生成降成串行 + 降低中途 `commit()` 频率
   - 连续生成模式下，要额外避免“当前章 finalize 的后台写入”和“下一章 generate”同时打库
3. **统一结构化输出入口。**
   - 新增一个 `StructuredLLMService` / `get_structured_response()`
   - 统一做：
     - 原生 JSON 输出
     - 清洗
     - schema 校验
     - 失败后的修复重试
4. **把“空响应 finish_reason=stop”当成可重试异常分类。**
   - 日志里单独打 tag
   - 响应里区分：
     - provider 空白返回
     - 安全策略拦截
     - 真正网络失败
5. **给 AI 评审和连续生成单独做止血。**
   - AI 评审：
     - 在高级生成链路里保持 best-effort，不允许把主流程带崩
     - 在独立评审接口里，把 provider 空响应和锁冲突转成明确错误态，不再直接抛 500
   - 连续生成：
     - 当前章 finalize 完成前，不启动下一章 generate
     - 增加单章失败分类提示，区分“模型空响应”和“本地库锁冲突”

### Phase 1：把核心工作流拉顺

目标：减少“抽卡感”和移动端摩擦。

1. **工作台改成移动端优先。**
   - 生成、确认、下一章、连续生成做成底部操作条
   - 侧边栏只保留章节导航和概览
   - 退出登录移到用户菜单，不跟创作主操作同层
2. **确认版本后自动推进。**
   - 默认行为：
     - 确认当前章
     - 自动聚焦下一章
     - 可选自动开始生成下一章
   - 提供开关，避免打断想手动检查的人
3. **大纲支持“锁定满意章节 / 重抽不满意章节”。**
   - `chapter_outline` 增加状态字段：
     - `draft`
     - `approved`
     - `needs_regen`
   - 支持：
     - 单章重生成
     - 多选重生成
     - 跳过已锁定章节
4. **把连续生成从“隐藏功能”变成主路径。**
   - 现在连续生成已经存在，但入口太隐蔽
   - 应直接在当前章成功后给“继续下一章”主按钮

### Phase 2：收敛后端架构

目标：减少双轨逻辑和后续维护成本。

1. **合并标准生成路由和 advanced 路由。**
   - 统一都走 `PipelineOrchestrator`
   - 标准路由只做参数兼容层
2. **修正评审链路。**
   - `/chapters/evaluate` 只保留一条语义清晰的链
   - 要么直接废弃 `evaluation.md`
   - 要么让它按 Issue #35 的要求吃结构化上下文
3. **补回归测试。**
   - SQLite 并发生成
   - 手动编辑章节
   - 大纲编辑后章节顺序
   - 结构化 JSON 失败修复

### Phase 3：长篇能力升级

目标：真正解决“只能抽卡”和长篇漂移。

1. `SceneWiseWriter`
2. 多 query RAG + rerank
3. 卷级记忆 / ArcSummary
4. Prompt 版本化和 section 级重跑

---

## 5. 建议执行顺序

### P0：先做

1. usage 计数脱离主事务
2. SQLite 并发保护
3. 统一结构化输出入口
4. 修评审输入契约（Issue #35）
5. 空响应分类与重试

### P1：紧接着做

6. 移动端工作台重排
7. 确认版本后自动推进下一章
8. 大纲逐章确认 / 重抽机制

### P2：随后做

9. 合并双轨生成路由
10. 补回归测试和 issue 回归清单

---

## 6. 验收标准

1. SQLite 环境下连续生成 10 章，不再出现 `database is locked` / `PendingRollbackError`
2. 结构化输出链路的 JSON 解析成功率达到可观测指标，失败时有统一错误码
3. 评审接口输入能带上蓝图/大纲/历史摘要，Issue #35 可关闭
4. 手机端完成“选章节 → 生成 → 确认 → 下一章”不再需要反复开侧边栏
5. 大纲支持“锁定满意章节 + 重抽不满意章节”
