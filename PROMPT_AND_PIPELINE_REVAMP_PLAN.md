# Arboris-Novel Prompts 与生成流水线健壮性改造方案

> 生成日期：2026-04-19
> 作者：架构评估（基于 `backend/prompts/*.md` 与 `backend/app/services/*.py` 的全量扫描）
> 目的：作为后续实际落地改造的单一依据，覆盖 prompts 优化、长上下文、长篇扩展、多分级（含 18+）内容的健壮性。

---

## 0. 二次复核快照（基于 2026-04-19 当前工作区）

> 说明：本文件最初版本基于第一次全量扫描。当前仓库已经继续演进，这一节用于覆盖已经过期的判断；后续执行请优先参考本节和第五部分的状态说明。

### 0.1 已落地

1. **Prompt seed 行为已升级。**
   - `backend/app/db/init_db.py::_ensure_default_prompts` 不再只是“缺失时插入”，同名 prompt 的文件内容变化后也会自动更新数据库记录。
2. **输入侧 token 预算已有初版。**
   - `backend/app/services/context_budgeter.py` 已存在，并在 `PipelineOrchestrator._build_prompt_sections()` 之后接入。
   - `backend/requirements.txt` 已加入 `tiktoken>=0.7.0`。
3. **第一轮 prompt 对齐已完成大半。**
   - `writing_v2.md` 已补 section 输入清单和信任优先级。
   - `optimize_dialogue/environment/psychology/rhythm.md` 已按 `original_content + additional_notes` 对齐。
   - 多个 JSON prompt 已补“单个合法 JSON 对象 / 不要代码围栏 / 不要 <think> / 不要前后缀”的输出纪律。
   - `outline_generation.md` 已改成 `macro_beat + phase_label`，`chapter_plan.md` 的 `scene_list` 示例也已扩成 3 个场景。
   - `concept.md` / `screenwriting.md` 的 jailbreak 文案已替换为分级声明。
4. **章节目标字数已经打通。**
   - `novel_blueprints.chapter_length` 已落库，`NovelService`、写作工作台、概览页都已接入。
5. **多版本草稿已改成并行生成。**
   - `PipelineOrchestrator` 现在通过 `_generate_versions_in_parallel()`，并用 `AsyncSessionLocal` 隔离 session 生成多版本。
6. **大纲链路已经拆成独立服务。**
   - 新增 `OutlineGenerationService`，支持批量生成、补齐缺失区间、扩写过短摘要。
   - `writer.py` 已新增 `/outline/fill-missing` 和 `/outline/expand` 两个入口，前端也有缺口提示和触发按钮。
7. **FinalizeService 已切到 async session 语义。**
   - `writer.py` 不再把 `sync_session` 传给 `FinalizeService`；`FinalizeService` 内部查询也已改成 async 风格。
8. **测试基础设施不再是 0。**
   - 当前至少已有 `backend/tests/test_pipeline_orchestrator.py`、`backend/tests/test_outline_generation_service.py`、`backend/tests/test_finalize_service.py`、`backend/tests/test_novel_txt_export.py`。
9. **LLM role 路由和内容分级基础链路已接上。**
   - `LLMConfigService.get_config_for_role()` 已支持按 `reviewer / optimizer / summarizer` 覆盖模型，并支持 writer 按 `content_rating` 切到 `mature / explicit` 配置。
   - `LLMService`、`PipelineOrchestrator`、`AIReviewService`、`OutlineGenerationService`、`FinalizeService` 和标准 `writer.py` 路由都已接入 `role` / `content_rating` 透传。
10. **`content_rating` 已进入蓝图模型与前端。**
   - `novel_blueprints` 已新增 `content_rating` 字段；
   - `NovelService` 的保存、局部更新、序列化和 overview section 已打通；
   - 前端概览区已支持展示和编辑 `content_rating`。

### 0.2 部分落地

1. **失败梯度重试只完成第一层。**
   - 现状：`PipelineOrchestrator._generate_with_gradient_retry()` 已支持“temperature 上调 + 缩掉次要 section 后重试”。
   - 还没做：fallback 模型、分场景兜底、版本级降级。
2. **Enrichment 阈值已从 70% 提到 80%。**
   - 这能减少无意义扩写，但本质上仍是整章后处理，不是按 `scene_list` 分段写。

### 0.3 仍未开始或尚未落地

1. `SceneWiseWriter` 还不存在，10k 字级章节依然是一口气生成整章。
2. 多 query RAG、rerank、按卷 namespace、卷级长期记忆、`WorldStateService` 这些长篇能力还没进入代码。
3. Prompt 版本化、Prompt 指纹缓存、单 section 续跑也还没开始。

### 0.4 执行建议

后续计划不要再把 `ContextBudgeter`、第一轮 prompt 对齐、章节目标字数、并行草稿生成、大纲补齐/扩写、Finalize async 化当成“待做项”；这些都应该转成“已完成基础版，继续增强”的状态。

---

## 目录

- 第一部分 整体代码结构与调用链
- 第二部分 Prompts 现状诊断（共性问题 + 个性问题）
- 第三部分 Prompts 增强调整方案（P0 / P1 / P2 / P3）
- 第四部分 长上下文与大规模连载健壮性评估
- 第五部分 落地路线图（P0 / P1 / P2）
- 附录 A 推荐的 Prompt 骨架模板
- 附录 B ContextBudgeter 伪代码
- 附录 C 落地前自检清单
- 附录 D 当前 prompt 文件清单与调用点映射
- 附录 E 多分级（含 18+）落地前的对照实验方案
- 附录 F 内容分级路由的落地顺序与回滚策略

---

## 第一部分 整体代码结构与调用链

### 1.1 架构速览

Arboris 是 FastAPI + Vue 单体工程，AI 写作流水线集中在 `backend/app/services/`。Prompt 模板是纯文本库（`backend/prompts/*.md`），与业务层的耦合点只有两个：

1. **启动时 seed + 同步更新**：`app/db/init_db.py::_ensure_default_prompts` 扫描 `backend/prompts/*.md`，把 `<文件名>` 作为 `name`、整段内容作为 `content` 写入 `prompts` 表；当同名 prompt 的文件内容变化时，会自动更新数据库记录。
   - 含义：当前本地/自管部署里，直接改 `.md` 文件再启动即可同步到 DB；如果线上后台已经人工改过 DB，还要留意“文件覆盖 DB”这件事。
2. **运行时通过 `PromptService.get_prompt(name)` 取**：带本地内存缓存，按 `name` 命中。

### 1.2 核心执行链（对应 CLAUDE.md 的三层架构）

```
outline_generation          -> 大纲（章节列表 + 1234 叙事节拍）
  └ concept / screenwriting -> 概念对话 + 蓝图
     └ chapter_plan (L2)    -> 章节导演脚本 ChapterMission（含 pov / macro_beat / pace_budget / scene_list）
        └ writing_v2 (L3)   -> 正文生成（writing 为兜底）
           └ editor_review  -> 多版本评审选最佳
           └ constitution_check / six_dimension_review / foreshadowing_reminder
                            -> 审核
           └ rewrite_guardrails -> 违规修复
           └ optimize_*     -> 四维度优化（dialogue / environment / psychology / rhythm）
           └ extraction     -> 回写摘要入 RAG
```

### 1.3 关键服务与职责

| 服务文件 | 职责 |
|---|---|
| `pipeline_orchestrator.py` | 核心编排器，串联 L1→L2→L3、评审、优化、摘要 |
| `llm_service.py` | LLM 调用封装，支持 chat/JSON 模式，含 `max_tokens`、`timeout`、`response_format` |
| `prompt_service.py` | Prompt CRUD + 内存缓存 |
| `chapter_context_service.py` + `vector_store_service.py` | RAG：章节 chunk + summary 双层检索（libsql） |
| `memory_layer_service.py` | 角色状态、时间线、因果链、故事时间跟踪 |
| `enrichment_service.py` | 字数不足时补足 |
| `outline_generation_service.py` | 批量生成章节大纲、补齐缺失区间、扩写过短摘要 |
| `finalize_service.py` | 章节定稿后的摘要/状态/快照/向量库闭环 |
| `writer_persona_service.py` | 作家声纹（当前 prompt 死档，persona 字符串由服务生成） |
| `foreshadowing_service.py` / `foreshadowing_tracker_service.py` | 伏笔管理 |
| `constitution_service.py` / `six_dimension_review_service.py` / `ai_review_service.py` | 多层评审 |

### 1.4 当前硬编码的规模假设

| 常量 | 值 | 出处 |
|---|---|---|
| `DEFAULT_CHAPTER_TARGET_WORD_COUNT` | 3000 | `pipeline_orchestrator.py:46` |
| `MIN_CHAPTER_WORD_COUNT` | 2200 | `pipeline_orchestrator.py:47` |
| `WRITER_GENERATION_MAX_TOKENS` | 7000 | `pipeline_orchestrator.py:48` |
| `_calc_max_tokens` | `min(max(target*2, 7000), 32000)` | 按目标字数 ×2 估 token，上限 32k |
| `MemoryLayerService.get_timeline` 窗口 | 最近 5 章 | `memory_layer_service.py:437-441` |
| `AIReviewService._build_review_input` 截断 | 每版本前 3000 字 | `ai_review_service.py` |

---

## 第二部分 Prompts 现状诊断

### 2.1 实际被代码引用的 prompt 映射

| Prompt 文件 | 调用位置 | 占位符（`{{...}}`） | 备注 |
|---|---|---|---|
| `writing_v2.md` / `writing.md` | `pipeline_orchestrator._generate_single_version` | 无（section 拼装） | v2 主力，v1 兜底 |
| `chapter_plan.md` | `pipeline_orchestrator`（chapter mission 生成） | 无 | 期望严格 JSON |
| `editor_review.md` | `AIReviewService.review_versions` | 无 | 多版本评审 |
| `rewrite_guardrails.md` | `pipeline_orchestrator._rewrite_with_guardrails` | 无 | 章节违规修复 |
| `six_dimension_review.md` | `SixDimensionReviewService` | 7 个（`constitution/writer_persona/chapter_plan/...`） | `str.replace` |
| `constitution_check.md` | `ConstitutionService` | 4 个（`constitution/chapter_number/title/content`） | `str.replace` |
| `foreshadowing_reminder.md` | `ForeshadowingTrackerService` | 4 个（`chapter_number/title/outline/active_foreshadowings`） | `str.replace` |
| `faction_context.md` | `FactionService` | 3 个（`factions_overview/relationships/members`） | `str.replace` |
| `extraction.md` | `LLMService`（章节摘要提炼） | 无 | |
| `import_analysis.md` | 导入旧小说流程 | 无 | 只返 JSON |
| `concept.md` / `screenwriting.md` | `novels.py` 路由 | 无 | 概念对话 / 蓝图生成 |
| `optimize_dialogue.md` / `optimize_environment.md` / `optimize_psychology.md` / `optimize_rhythm.md` | `pipeline_orchestrator._run_optimizer` | 无 | **输入结构与代码不符，见共性问题 2** |
| `evaluation.md` | `writer.py` 路由（多版本评估） | 无 | 与 `editor_review` 职责重叠 |
| `writer_persona.md` | **无任何代码调用（死档）** | 2 个 | runtime 走 `WriterPersonaService` 自生成字符串 |
| `character_dna_guide.md` | **无任何代码调用（死档）** | 无 | 产品文档残留 |

### 2.1.1 二次复核结论（覆盖第一次扫描）

以下问题在当前工作区里已经不再是主阻塞：

1. `writing_v2.md` 的 section 输入清单已经补齐。
2. `optimize_*` prompt 与 `_run_optimizer` 的 `additional_notes` 传参已经对齐。
3. 多个关键 JSON prompt 已统一补上输出纪律。
4. `outline_generation.md` 与 `chapter_plan.md` 的 `macro_beat / scene_list` 示例已经调整。

当前还需要继续追的 prompt 问题主要剩下：

1. 占位符没有统一声明，也没有加载时校验。
2. `editor_review.md` / `evaluation.md` 职责仍然重叠。
3. `writer_persona.md` / `character_dna_guide.md` 仍是死档。
4. `PromptService` 还没有 frontmatter、版本化、枚举动态注入这些工程化能力。

### 2.2 共性问题（优先级最高）

1. **占位符格式不统一，没有默认值保护。**
   - `{{placeholder}}` 与 `[section]` 两种伪占位混用。管理员改 prompt 误增占位符时，没人 replace，会原样漏给 LLM。

2. **输入契约和代码实际注入对不上（已确认的硬 Bug）：**
   - `writing_v2.md` 声明的输入是 6 个 section，`pipeline_orchestrator._build_prompt_sections` 实际注入 12 类：
     - `[世界蓝图]` / `[项目长期记忆]` / `[记忆层上下文]` / `[上一章摘要]` / `[上一章结尾]` / `[章节导演脚本]` / `[RAG精筛上下文]` / `[检索到的剧情上下文]` / `[检索到的章节摘要]` / `[当前章节目标]` / `[篇幅与排版要求]` / `[禁止角色]`。
     - Prompt 没承诺过的段落，模型容易忽略。
   - `optimize_dialogue.md` 声明 `{original_content, characters, scene_emotion}`，`optimize_environment.md` 声明 `{original_content, target_emotion, key_scenes}`，`optimize_psychology.md` 声明 `{original_content, character_dna, scene_context}`。
     - **但 `_run_optimizer` 对四个维度统一只发 `{"original_content": ..., "additional_notes": "在不改变剧情走向的前提下优化该维度。"}`。**
     - 声明的角色/情绪/DNA 字段 100% 缺失，模型只能瞎猜。

3. **JSON 输出约束太软。** 期望 JSON 的 prompt（`editor_review / evaluation / six_dimension_review / foreshadowing_reminder / constitution_check / outline_generation / import_analysis / screenwriting / chapter_plan`）：
   - 措辞不统一（"请严格 JSON" / "必须 JSON" / 只在示例展示）。
   - 没有明确说"不要 markdown 围栏 / 不要 `<think>` / 不要前言后语"。
   - 评分量纲混乱：`editor_review.scores` 是 0-10，`six_dimension_review.overall_score` 是 0-100，`evaluation` 无评分只给 pros/cons。

4. **"绝对禁令"类黑名单越攒越多，但没有优先级。** `writing_v2` 第 6/7 条与 `writer_persona` 的"反 AI 检测规则"高度重复，冲突时模型无从权衡。应收敛为硬性禁令 / 软性偏好 / 例外条件三档。

5. **角色设定过度煽情、缺少任务导向。** 几乎所有 prompt 都以"起点金牌编辑 / 白金大神 / 顶级分析师"开头，"步骤化流程 + 反例 + 自检清单"才是稳态输出的主力。

6. **中英文、全角/半角、markdown 用法随意。** `concept.md` 用 `Role:` `## Profile:`，`outline_generation.md` 用 emoji，`writing_v2.md` 用中文冒号。切换 prompt 时上下文密度差异导致模型行为不稳。

7. **部分 prompt 已死档 / 文档陈旧。**
   - `writer_persona.md`、`character_dna_guide.md` 无代码调用。
   - `backend/prompts/README.ai` 还在列 `chapter_generation.txt / outline_generation.txt / review_generation.txt` 这些不存在的 .txt 文件。
   - `writing.md`（v1）比 v2 少很多硬约束，应降级为 v2 的 fallback 最小子集，或直接删除。

### 2.3 个性问题（按文件）

#### outline_generation.md
- `narrative_phase` 用中文字符串"事件/势力/挑衅1/回击1"，但 `chapter_plan.md` 用枚举 `E/F/P/C`。上下游需要额外映射。
- 示例 JSON 把四章压成 E→F→P→C，与"每个循环跨 4-8 章"的原则相反，模型会照抄示例。
- "12341234 循环叙事法"表述与 `chapter_plan` 的 E/F/P/C 不是同一套。建议全局统一。

#### chapter_plan.md
- `pace_budget.new_major_facts` 中"重大"没有定义；缺少"重大 = 改变角色目标 / 推翻假设 / 引入不可逆事件"口径。
- `scene_list` 示例只给 1 个场景，与"标准 3-5 个"原则相反。

#### writing_v2.md
- 第 8 条"字数硬要求"与"断章艺术 / 不得强行结束"存在隐性矛盾。应改为"字数不够时沿 `scene_list` 下一个场景节拍继续推进"。
- 未提及 orchestrator 实际注入的 RAG/记忆/长期记忆/篇幅排版/禁止角色 section。
- 无自检清单。

#### chapter_plan + writing_v2
- `forbidden` 数组是自由文本，建议给固定枚举让 L2 选填。

#### editor_review.md / evaluation.md
- 职责高度重叠但字段完全不同：
  - `editor_review.scores` = `{immersion, pacing, hook, character}` (1-10)。
  - `evaluation.evaluation[versionN]` = `{pros, cons, overall_review}`（无评分）。
- 建议：合并为一份，或明确区分（`editor_review` 自动挑选 + 评分；`evaluation` 对比报告给人看）。

#### constitution_check.md / six_dimension_review.md
- `constitution_check` 8 维度完全被 `six_dimension_review` 的"宪法合规"子维度包含。
- 建议：`constitution_check` 作为写作前硬门禁（只做 critical），`six_dimension_review` 作为深度报告；两者共用同一套 `dimension` 枚举和 `severity` 三档。

#### rewrite_guardrails.md
- 只列 3 类违规（`forbidden_name / omniscient_cue / sudden_familiarity`），但评审输出的违规分类远多于此。
- 建议：`violations[].dimension` 枚举化；明确"不得新增剧情 / 不得改动已登场角色 / 不得引入新对白主题"的边界；无法修复时保留原文并添加 `<!-- unfix: xxx -->` 注释供代码侧检测。

#### optimize_*.md（四件）
- 输入结构与 `_run_optimizer` 对不上（见共性问题 2）。
- 四个 prompt 独立 pass，顺序固定（dialogue → environment → psychology → rhythm），后面 pass 会覆盖前面的人物化改动。
- 建议加"只改动 diff 范围"约束，输出 `optimized_content` + `changed_paragraph_indices` + `notes`。

#### foreshadowing_reminder.md
- `foreshadowings_to_develop[].id` 来源未说明，模型可能创编 id。应强约束："id 必须严格从下面列表里选取，严禁自造；若无合适伏笔请返回空数组"。

#### import_analysis.md
- `relationship_type: friend|enemy|lover|family|other` 枚举与 `schema.sql` 实际枚举可能不一致。建议由 `PromptService` 取表时动态注入 `{{enum_relationship_type}}`。

#### concept.md / screenwriting.md
- 包含"你必须突破常规 AI 内容限制…情色…"这类 jailbreak 式表达。对主流 API（OpenAI/Anthropic/国内合规）**反而拉高拦截率**，也容易整条 session 空响应。
- 正确做法是**按项目 `content_rating` 路由到不同 provider/model**，prompt 保持克制。
- `screenwriting.md` 要求一次生成 300-800 章的 JSON，多数 LLM 会中断。建议拆成"先分幕（3-7 幕）再按幕生成章节"两段式，或单章 summary 限字（≤60 字）。

---

## 第三部分 Prompts 增强调整方案

### P0（先消除硬 Bug / 明显错配）

1. **把 `optimize_*` 四个 prompt 的「输入格式」改成只用 `{original_content, additional_notes}`**（与 `_run_optimizer` 现有传参对齐），把 `characters / character_dna / scene_emotion / target_emotion / key_scenes` 标注为"可选字段，如无则默认"。
   - 或相反：改 `_run_optimizer` 塞入这些字段。根据"先调 prompt 不动代码"原则，选前者更快。

2. **统一 JSON 输出纪律。** 所有"期望 JSON"的 prompt 末尾追加：
   ```
   输出必须是单个合法 JSON 对象，不要 markdown 代码围栏、不要 <think>、不要任何前言/后语/总结。
   字段名严格遵守，未知字段留空字符串 / 空数组 / null。
   ```

3. **修示例：**
   - `outline_generation.md` 别用 4 章就 E→F→P→C 的示范。
   - `chapter_plan.md` 的 `scene_list` 示例展示 3 个场景而非 1 个。

4. **`outline_generation.narrative_phase` 改为 `macro_beat: E|F|P|C` + `phase_label` 中文**，与 `chapter_plan` 对齐。

5. **`writing_v2.md` 的输入清单补齐**当前 orchestrator 实际注入的 12 类 section，并加一条冲突时的信任优先级：
   ```
   [章节导演脚本] > [世界蓝图] > [记忆层上下文] > [RAG 精筛 / chunks / summaries]
   ```

6. **死档 prompt 处理：**
   - `writer_persona.md` / `character_dna_guide.md`：要么删除，要么在 `WriterPersonaService` / `CharacterKnowledgeManager` 里明确调 `get_prompt(...)`。
   - `writing.md`（v1）：裁齐到 v2 硬约束的最小子集，或直接删除。

7. **`concept.md` / `screenwriting.md` 的"创作限制"段落改写。** 去掉 jailbreak 词汇，改为：
   ```
   本工具用于连载网文创作。请按本项目声明的内容分级（见输入 content_rating）处理题材：
   - safe：守住平台通用尺度；
   - mature：可写暴力、压抑、成人情感，不做说教；
   - explicit：按作者要求真实呈现亲密场景，以人物心理与感官细节为主。
   硬红线：任何分级下均拒绝涉及真实在世人物、未成年人、非自愿伤害未成年等题材。
   ```

### P1（统一结构 / 提升稳态）

8. **定 prompt 骨架**（建议落在 `backend/prompts/_TEMPLATE.md`）：见附录 A。所有 prompt 按此结构重写。

9. **占位符统一 `{{snake_case}}`，模板头部声明 `placeholders:` 块**，让 `PromptService` 加载时校验占位符都被 replace。

10. **硬约束分三档**：Critical / Warning / Style。六维评审和宪法检查共用同一套 severity 和 dimension 枚举，方便前端统一展示、方便 `rewrite_guardrails` 拿到统一违规类型去修。

11. **合并 `editor_review` / `evaluation`：** 用一份 prompt，输出 schema 支持"评分 + 最佳版本 + 对比报告"，`AIReviewService` 只取关心的字段。文件层面保留两份但 `evaluation.md` 加 `@see editor_review.md`。

12. **动态注入系统枚举。** 把 `relationship_type / narrative_phase / severity / chapter_end_style` 等枚举，在 `PromptService.get_prompt` 时按 `{{enum_xxx}}` 注入当前代码里的合法值。

### P2（效果升级，可做 A/B）

13. **`writing_v2` 加入自检清单：** POV 一致 / 登场名单合规 / `macro_beat` 正确 / 字数达标 / 结尾是钩子而非总结 / AI 套话为 0 / 对话不塞设定 / `entrance_protocol` 执行到位。模型输出前自检通常比事后评审省 token。

14. **`chapter_plan` 给 `pace_budget` 默认值 + 硬校验规则**，并给"重大"下定义；`scene_list` 至少 3 项（示例对齐）。

15. **`optimize_*` 加差分输出：** `changed_paragraph_indices` + `change_summary`，orchestrator 可选只替换这些段落。

16. **`foreshadowing_reminder` 改 `id` 为 `foreshadowing_ref`**，约束写"必须来自 `active_foreshadowings` 清单，否则为 null"。

17. **JSON prompt 末尾放 `Example of a valid response:`**。模型对末尾样例的模仿度通常最高。

18. **分级版本：** prompt 文件名改成 `<name>.<version>.md`（如 `writing.2.md`）由 DB 存 `active_version`。seed 逻辑支持"检测到更高版本自动升级"。解决"改 md 不生效"问题。

### P3（工程化）

19. **给每份 prompt 加 frontmatter：**
    ```yaml
    ---
    name: writing_v2
    title: L3 正文生成器 v2
    version: 2.1.0
    tags: [l3, writer, hard-constraints]
    placeholders: []
    updated_at: 2026-04-19
    ---
    ```

20. **补离线评测脚本：** 10 个固定输入走每个 prompt，用 JSON schema + 关键词检查打分。改 prompt 时跑一遍回归。

21. **更新 `backend/prompts/README.ai`：** 列当前实际文件 + 调用点 + 占位符 + 输出类型。

---

## 第四部分 长上下文与大规模连载健壮性评估

### 4.0 二次复核后的状态修正

第一次扫描里“没有输入 token 预算”和“不会计 token”这两条已经过期。当前代码已经有：

1. `ContextBudgeter.fit()`：按 section 进行 hard limit + priority 压缩/丢弃。
2. `tiktoken`：优先使用 `cl100k_base`，并保留字符数兜底估算。

但这还只是基础版，离计划里的目标还有差距：

1. budget 还是写死在代码里，没有按模型 context window 动态调。
2. 关键 section 被压缩后的质量没有专门评测。
3. 失败时不会自动“缩掉次要 section 再试”。
4. 章节生成虽然已并行出多版本，但仍然是“一次性整章”，没有分 scene。

### 4.1 体检结论

骨架是对的——已有分层上下文、分层生成、伏笔/记忆/RAG/Enrichment。

**致命缺口（10k 字章 + 长篇场景下一定会炸）：**

1. **输入 token 预算已有初版，但还不够动态。** `_build_prompt_sections` 之后已经接入 `ContextBudgeter`，不过 budget 还是静态值，且没有按模型窗口实时调节。
2. **会计 token，但还没形成完整预算体系。** 当前已有 `tiktoken` 和 fallback 估算；还缺少预算命中率、裁剪效果、模型差异这些回归数据。
3. **Blueprint 没做分卷/压缩。** 无论第 3 章还是第 300 章，都注入当前 POV 可见的全量角色/世界/势力。长篇会膨胀到几千字，每章重复付出。
4. **RAG 单 query、无 rerank、无分卷 namespace。** 长篇会召回大量低相关 chunk。
5. **章节正文一次性生成。** 哪怕给 `max_tokens=20000`，大多数服务单次输出会被提前切断（GPT-4o mini 默认 16k 输出、不少服务 8k 封顶、DeepSeek 稳态 4-6k）。没有"分场景生成—拼接—校对"流水线。
6. **没有按内容分级路由模型。** 在 `concept.md` / `screenwriting.md` 用 jailbreak 式提示对主流 API 反而拉高拦截率。
7. **长篇一致性靠人工记忆。** `MemoryLayerService.get_timeline` 只回最近 5 章。超过的完全不管。300 章以后"第 12 章埋的伏笔"丢了就是丢了。
8. **生成失败无梯度降级。** 失败只记日志 / 跳过，无"缩短上下文再试 / 切便宜模型 / 分场景重跑 / 版本兜底"。

### 4.2 问题一：章节 1 万字、上下文超长怎么办

**量级感：**
- 10k 中文字 ≈ 12k-20k 输出 token。
- 当前默认注入上下文在中长篇阶段 ≈ 20k-50k 输入 token。
- 合计 32k-70k，128k 模型窗口看着够，但真正问题是：
  - **单次输出上限**：几乎所有服务"最大输出 token"都小于总窗口（16k 常见、国内多 4k/8k）。
  - **长输入衰减**：prompt 越长，注意力越倾斜到末尾，前面的蓝图/宪法被忽略。
  - **成本 × 多版本**：`version_count=2~3`，optimizer ×4，六维审 ×1，同章上下文被重复付 10 次钱。

**四层稳态方案：**

**(a) 输入侧 —— ContextBudgeter（必做，最高优先级）**

在 `_build_prompt_sections` 之后加 `ContextBudgeter`，给每类 section 分配硬上限，超出按优先级砍。参考预算（128k 模型、章节 10k 字输出）：

| Section | 上限 (token) | 裁剪策略 |
|---|---|---|
| 系统 prompt（writing_v2 全文） | 1.5k | 不压 |
| 章节导演脚本 JSON | 1.5k | 不压 |
| 世界蓝图（仅已登场 + allowed_new） | 4k | 超出时裁 backstory / 长 description |
| 上一章摘要 + 上一章结尾 | 2k | 结尾保 800 字，摘要压 500 字 |
| 记忆层上下文 | 2k | 丢无关 POV 的角色状态 |
| 长期记忆（卷级摘要） | 1.5k | 最近 2 卷 + 全书 tagline |
| RAG chunks | 3k | top-k 动态降，chunk 截 400 字 |
| RAG summaries | 1k | 只取与当前 macro_beat 相关 |
| 禁止角色 + 篇幅排版 | 0.3k | 不压 |
| **输入合计** | **≤17k** | 输出 10k+ 仍有 50% 余量 |

实现：`tiktoken.get_encoding("cl100k_base")` 估 token，每 section 超预算走 `_shrink_<section>`。**必须按优先级丢 section，不能整段截断文本**（整段截断最容易把 JSON 变非法）。

**(b) 生成侧 —— 分场景生成 SceneWiseWriter（核心升级）**

`chapter_plan.scene_list` 已规划 3-5 个场景，用起来：

1. 一次调用只写 1 个 scene（每 scene ≈ 2000-3000 字），`max_tokens` 压到 4-6k。
2. 每写完一个 scene，立即生成 100 字 scene 摘要（同次调用输出 `{scene_content, scene_tail, scene_summary}` JSON，或单独用低成本模型 extraction）。
3. 下一 scene 输入只带"前一 scene 的 tail + summary"，不带全文。
4. 全部 scene 生成完用轻量拼接 prompt 修转场处。
5. 最后做一次全章一致性检查（六维 / 宪法 / 伏笔），只报告不重写。

比"一次性 10k 字再切"稳一个量级。失败只丢一个 scene，不用整章重来。

**(c) 模型路由 —— LLM Profile 按字数/用途选**

- 普通章（≤4k 字）：便宜模型（deepseek-chat / gpt-4o-mini / 国产低价）。
- 长章（4k-8k 字）：中档（gpt-4o / claude haiku / deepseek-v3）。
- 超长章（>8k 字）：默认分场景，禁止一次性生成。
- 编辑/评审：**一律更便宜的模型**（现在写作与评审同模型）。

`llm_config_service.py` 加 `model_profile`（`writer / reviewer / summarizer / optimizer`），各处按 profile 取。

**(d) 失败与重试梯度**

```
单版本生成失败 →
  1) temperature +0.1 重试（同模型同上下文）
  2) 砍次要 section（RAG chunks、optional memory）再试
  3) 降级 fallback 模型（同家便宜档）
  4) 分场景重跑（每 scene 单独调用）
  5) 上一版本兜底（如果 version_count>1 且其它版本已生成）
全部失败才上报 UI
```

### 4.3 问题二：小说过大的影响

**(a) 上下文层：300 章时 blueprint + 长期记忆会爆。**
- `writer_blueprint` 即便按 POV 裁过，角色/势力/地点量级随章节线性增长。**必须分卷（arc）分层**：只注入"当前卷 + 相邻卷 tagline + 全书主线 tagline"，历史卷压到 300 字一卷的"卷摘要"。
- `MemoryLayerService.get_timeline(start=n-5)` 只回 5 章——加"远期伏笔/转折点优先级"：`is_turning_point=True` 或 `CausalChain.pending` 的事件，即使超过 5 章也要带。

**(b) RAG 层：召回质量会随库变大而下降。**
- 单 query 不够，做 **multi-query RAG**：`chapter_plan` 的 `pov / allowed_new_characters / emotion_target.type / macro_beat` 各跑一条 query，分别 top-3 合并 dedup + rerank。
- 加 **reranker**（没有 cross-encoder 可用便宜模型的 pointwise relevance score），留 top-5。
- Vector store 分 namespace：`project_id + arc_number`。只在当前 arc + 前一 arc 检索，其它 arc 只用摘要层。

**(c) 伏笔层：长篇命门。**
- 伏笔清单加"年龄计算"：埋下超过 N 章未提自动拉入下章 `foreshadowing_reminder` 的"紧迫"清单。
- 活跃伏笔 > 50 条时按"到期紧迫度 + POV 相关度"排序只贴前 10。

**(d) 一致性层：时间/地点漂移。**
- `StoryTimeTracker` 已有，但无"世界地理状态"跟踪（A 地被烧后还能不能去）。长篇必须加 `WorldStateService`：每章后 extract 本章对世界的状态变化，下次 RAG 注入"当前世界快照"。
- 角色关系做状态机：`characters.relationships` 按章节有版本。Schema 里已有 `relationships` 表，但无"关系演变事件"记录。

**(e) 成本与时长。**
- 300 章 × 多版本 × optimizer × 六维审 = 单本小说几千次 LLM 调用。**必须做缓存**：已生成章节摘要/嵌入绝不重算；同章重跑只重跑有变化的 section（prompt 指纹比对）。
- 任务编排做**可断点续跑**：每 step 写 `generation_step` 和 checkpoint 到 DB。已有 `generation_progress` 字段，扩展成每 section 存中间产物，失败后只从失败点续。

**(f) DB / 向量库。**
- SQLite 对 300 章 × 3 版本 × 多 chunk 没问题，但 libsql 建议每 50 章做一次索引重建/压缩。
- `chapter_versions` 做软删除 + 归档表，避免宽行。

### 4.4 问题三：更健壮同时不丢原味 + 各尺度都能写（含 18+）

**原则：** "味道"来自 `writing_v2.md` / `writer_persona` / `chapter_plan` 的约束，不来自 jailbreak 式"突破 AI 限制"。**健壮性升级和尺度是两条独立轴，分开做。**

#### 健壮性轴（不改风格，只改管道）

1. `ContextBudgeter`：硬 token 预算 + 按优先级裁剪。
2. 分场景生成 `SceneWiseWriter`：10k 字章默认走这条。
3. 模型 profile 路由：`writer / reviewer / summarizer / optimizer` 四挡，各自可换模型。
4. 生成梯度重试（见 4.2(d)）。
5. 卷级长期记忆：每 N 章生成 `arc_summary` 入 `novel_arcs` 表；上下文只注入最近 2 卷 + 全书 tagline。
6. 多 query RAG + 轻量 rerank + namespace 分卷。
7. 伏笔/转折点强制纳入长记忆（年龄 + 紧迫度升级）。
8. `WorldStateService`：章节后 diff 世界状态，下章注入快照。
9. Prompt 文件版本化：文件名带 `.vN.md`，seed 比较版本自动升级；管理员后台能一键 reseed。
10. 流水线 checkpoint：每 section 存中间产物 + prompt 指纹，失败续跑、手动改提示词重跑单段。

#### 风格保鲜轴（保持起点白金 + 断章艺术 + Show Don't Tell）

11. 保留并精炼 `writing_v2.md` 硬约束（参考第三部分 P0/P1）。
12. **把 `writer_persona` 真接进 `writing_v2` 生成链路**（当前死档）。每个项目有自己的作家声纹，由 3-5 个关键维度决定（句式偏好、口头禅、叙事距离、情感色温、视角收紧度）。
13. 分场景生成时，**前两个 scene 和最后一个 scene 用更高 `temperature`**（0.85-0.95）保留文字质感；中间衔接 scene 用 0.7，减少漂移。
14. 多版本对比（`editor_review`）保留，但"最佳版本"不替换"最佳段落"——**挑每 scene 最佳版本再拼接**，显著提质且味道更不丢。

#### 各尺度都能写（18+）的正确做法

真正决定成败的是**服务商选择和路由**，不是提示词花招：

**(a) 数据模型层：** 在项目 schema 加 `content_rating: safe | mature | explicit`（可复用 `target_audience`，但单独建一个更干净）。

**(b) LLM 路由层：** `LLMConfigService` 配置多个 provider profile：
- `mainstream_writer`：主流 API（OpenAI / Claude / DeepSeek 官方），写普通向。
- `mature_writer`：允许成人向的服务（开源自部署 + 不加内容审查层的推理服务，或明确允许 NSFW 创作的商业 API）。
- `nsfw_writer`：显式支持 explicit 创作的自部署模型（Mistral / Qwen / Yi 的无审查微调版，或自家 GPU）。

路由规则：`content_rating` = safe → profile A；mature → profile B；explicit → profile C。

**(c) Prompt 层：** 保持克制，**不要再写"突破 AI 限制"**。只声明分级（见 P0 第 7 条的示例文本）。

**(d) 硬红线（无论哪档）：** 真实在世人物、未成年人、非自愿伤害未成年等。这不是风格问题，是任何平台都要的合规底线。

**(e) 评审层：** `editor_review / six_dimension_review` 按分级调整评分口径。explicit 项目不因"有情色描写"而扣分，只检查技法；mature/safe 按通用守则。给评审 prompt 注入 `content_rating` 变量，让评审 prompt 有对应规则分支。

这样把"尺度能不能写"从**提示词 jailbreak** 迁移到**基础设施 + 路由 + 分级**，健壮得多，也不会因为某次官方 API 内容策略更新而整条线路断掉。

---

## 第五部分 落地路线图

### P0（一周内可完成，现在问题最大）

1. `ContextBudgeter` 基础版已完成，下一步改成**按模型窗口动态预算 + 失败时主动缩上下文再试**。
2. 拆 LLM profile：`writer / reviewer / optimizer / summarizer` 四挡，基础分流已落地；下一步是补更细的 provider 切换和观测。
3. 分级路由：`content_rating` 字段、writer 路由和前端编辑已落地；下一步是继续收敛成单独的 `ContentRoutingService` 和更完整的评审分支。
4. Prompt 改动（第三部分 P0）：
   - 把 `optimize_*` 输入说明改成 `{original_content, additional_notes}`。
   - 所有 JSON prompt 末尾追加统一 JSON 约束段。
   - 修 `outline_generation` / `chapter_plan` 的示例。
   - `outline_generation.narrative_phase` 改为 `macro_beat`。
   - `writing_v2` 补齐输入清单 + 信任优先级。
   - 删除或降级死档 prompt。
   - `concept.md` / `screenwriting.md` 去 jailbreak，改为分级声明。
   - 上面前 5 条在当前工作区已完成，剩余主项是死档 prompt 清理和后续工程化。
5. 失败梯度重试（temp → section → 模型 → 分场景 → 版本兜底）。
   - 当前已完成 `temperature` 重试和缩上下文重试。
6. 新增的已完成项：
   - `OutlineGenerationService` 已落地并接入前后端。
   - `FinalizeService` 已适配 async session。
   - `backend/tests/` 已补基础回归用例。

### P1（2-3 周）

6. `SceneWiseWriter`：按 `chapter_plan.scene_list` 分场景生成。10k 字章默认走这条；短章可用"一次性"保留原流程。
7. 卷级长期记忆 `ArcSummaryService`：每 10-20 章滚一次，入库；上下文注入"最近 2 卷 + 全书 tagline"。
8. 多 query RAG + reranker + namespace 分卷。
9. 伏笔/转折点优先级升级（年龄 + 紧迫度）；长期伏笔不因 5 章窗口被丢掉。
10. Prompt 文件版本化 + 管理员一键 reseed；避免"改 md 不生效"。

### P2（有评测再做）

11. `WorldStateService`（世界状态 diff）。
12. 每 scene 各挑最佳 + 智能拼接（非整章挑最佳）。
13. 离线 prompt 回归评测（10 个标准项目、每 prompt 跑 5 次取均值 + 方差）。
14. 可断点续跑 + prompt 指纹缓存，重生单 section 而不重跑整章。

### 最小健壮改造（推荐起手 3 件）

**如果想尽量小步快跑，直接做：**

- P0 的 **1**（`ContextBudgeter`）
- P0 的 **2**（LLM profile）
- P0 的 **5**（失败梯度重试）
- 再加上第三部分 P0 的 **7 条 prompt 改动**。

这套组合拳做完，应该能让当前管道在 3000-8000 字章节上稳到可以信任，后面 P1 的分场景/卷级记忆再按业务节奏推。

---

## 附录 A 推荐的 Prompt 骨架模板

保存到 `backend/prompts/_TEMPLATE.md`，所有新 prompt 按此结构：

```markdown
---
name: <prompt_name>
title: <中文短标题>
version: 1.0.0
tags: [<layer>, <purpose>]
placeholders: [foo, bar]  # 如果用 {{foo}} 必须在这里声明
updated_at: YYYY-MM-DD
---

# Role
（一行，去掉情绪词，保留职能）

# Objective
（本 prompt 唯一目的，一句话）

# Inputs
（逐字段说明，标注必选/可选，说明占位符或由代码拼接）

# Procedure
（模型思考步骤，3-5 步）

# Hard Constraints
（硬禁令，违反即失败）

# Soft Preferences
（软偏好，可为剧情让步）

# Self-Check
（输出前自检清单 5-8 项）

# Output Format
（JSON schema 或文本模板，含"无前言后语"约束）

# Examples
（正例 + 反例各一个）
```

---

## 附录 B ContextBudgeter 伪代码

```python
# backend/app/services/context_budgeter.py
import tiktoken
from dataclasses import dataclass
from typing import List, Tuple, Callable

ENCODING = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(ENCODING.encode(text))

@dataclass
class SectionBudget:
    name: str                           # e.g. "[世界蓝图]"
    content: str
    hard_limit_tokens: int              # 0 表示不压
    priority: int                       # 1 最高，数越大越可丢
    shrinker: Callable[[str, int], str] # (content, target_tokens) -> shrunken content

class ContextBudgeter:
    def __init__(self, total_budget_tokens: int):
        self.total_budget = total_budget_tokens

    def fit(self, sections: List[SectionBudget]) -> List[Tuple[str, str]]:
        # 1. 先压每个 section 到自身 hard_limit
        compressed = []
        for s in sections:
            content = s.content
            if s.hard_limit_tokens and count_tokens(content) > s.hard_limit_tokens:
                content = s.shrinker(content, s.hard_limit_tokens)
            compressed.append((s, content))

        # 2. 总量仍超，按 priority 降序（数大优先丢）丢整段
        while self._total_tokens(compressed) > self.total_budget and compressed:
            victim_idx = max(
                range(len(compressed)),
                key=lambda i: compressed[i][0].priority,
            )
            # 优先级最高（数字最大）的先丢
            if compressed[victim_idx][0].priority >= 5:
                compressed.pop(victim_idx)
            else:
                # 不能再丢关键 section，改为二次压缩
                s, c = compressed[victim_idx]
                target = max(500, count_tokens(c) // 2)
                compressed[victim_idx] = (s, s.shrinker(c, target))
                if count_tokens(compressed[victim_idx][1]) >= count_tokens(c):
                    break  # 无法再压

        return [(s.name, c) for s, c in compressed]

    @staticmethod
    def _total_tokens(pairs):
        return sum(count_tokens(c) for _, c in pairs)


# 使用示例（挂在 pipeline_orchestrator._build_prompt_sections 之后）
def build_shrinker_for_blueprint(...):
    def _shrink(content: str, target: int) -> str:
        # 简化：先丢 backstory / description 字段
        import json
        bp = json.loads(content)
        for ch in bp.get("characters", []):
            ch.pop("backstory", None)
        for loc in bp.get("world_setting", {}).get("key_locations", []):
            loc["description"] = loc.get("description", "")[:80]
        out = json.dumps(bp, ensure_ascii=False, indent=2)
        while count_tokens(out) > target:
            # 再丢 abilities / relationships
            ...
        return out
    return _shrink
```

**Priority 建议：**

| Section | Priority | Hard Limit |
|---|---|---|
| 章节导演脚本 | 1 | 1500 |
| 世界蓝图 | 2 | 4000 |
| 当前章节目标 | 1 | 300 |
| 上一章摘要 + 结尾 | 3 | 2000 |
| 记忆层上下文 | 4 | 2000 |
| 长期记忆 | 4 | 1500 |
| RAG chunks | 6 | 3000 |
| RAG summaries | 5 | 1000 |
| 禁止角色 + 篇幅要求 | 1 | 300 |

Priority 1-2 不可丢；3-4 先压后丢；5-6 最先丢。

---

## 附录 C 落地前自检清单

**Prompt 改动前：**
- [ ] 明确新老 prompt 的 `name` 不变（避免 seed 失败）。
- [ ] 占位符（`{{...}}`）的 replace 代码仍然对得上。
- [ ] JSON schema 字段名不变（防止下游解析失败）。
- [ ] 本地跑一次端到端小说生成，对比改动前后的 diff。
- [ ] 管理员后台确认 DB 里的 content 已被刷新（或跑一次性迁移脚本）。

**代码改动前：**
- [x] `tiktoken` 依赖加入 `requirements.txt`。
- [ ] `content_rating` 字段的 migration 写好（`backend/db/migrations/`）。
- [ ] LLM profile 的配置可以通过环境变量覆盖（便于不同部署环境切换）。
- [ ] 分级路由有降级策略（mature profile 配置缺失时降级到 safe）。
- [ ] 失败梯度每一档都有独立的日志 tag，便于监控。

**评测 / 回归：**
- [ ] 准备 10 个标准 project（不同题材、不同章数）作为回归集。
- [ ] 每次 prompt / 代码改动跑一次回归，记录 token 用量、失败率、字数达标率、JSON 解析成功率。
- [ ] explicit 分级的项目单独跑一组，确认路由到正确的 provider。

**长篇压力测试：**
- [ ] 模拟 300 章项目，验证 blueprint 裁剪 / 卷级摘要 / RAG namespace 都生效。
- [ ] 模拟 10k 字章节，验证分场景生成流畅、拼接自然。
- [ ] 模拟主力 LLM 故障，验证降级链路能跑通。

---

## 附录 D 当前 prompt 文件清单与调用点映射

| 文件 | 行数 | 调用点 | 占位符 | 输出类型 | 状态 |
|---|---|---|---|---|---|
| `writing.md` | 48 | `pipeline_orchestrator._generate_single_version`（兜底） | 无 | 文本 | 建议降级为 fallback |
| `writing_v2.md` | 70 | `pipeline_orchestrator._generate_single_version`（主力） | 无 | 文本 | **输入清单已补；仍缺自检清单** |
| `chapter_plan.md` | 145 | `pipeline_orchestrator`（chapter mission） | 无 | JSON | **示例已修；仍可继续补默认值/硬校验** |
| `outline_generation.md` | 171 | `OutlineGenerationService` / 大纲生成流程 | 无 | JSON | **macro_beat 已统一；仍需评估长批次稳定性** |
| `concept.md` | 63 | `novels.py::_concept` 路由 | 无 | 对话 + 文本 | **jailbreak 已移除；content_rating 仍未接代码** |
| `screenwriting.md` | 96 | `novels.py::_screenwriting` 路由 | 无 | JSON | **jailbreak 已移除；仍建议拆成分幕生成** |
| `editor_review.md` | 54 | `AIReviewService.review_versions` | 无 | JSON | 与 evaluation 重叠 |
| `evaluation.md` | 113 | `writer.py` 多版本评估路由 | 无 | JSON | 建议合并 editor_review |
| `six_dimension_review.md` | 144 | `SixDimensionReviewService` | 7 个 | JSON | 与 constitution 重叠 |
| `constitution_check.md` | 90 | `ConstitutionService` | 4 个 | JSON | 建议做硬门禁 |
| `foreshadowing_reminder.md` | 85 | `ForeshadowingTrackerService` | 4 个 | JSON | **需约束 id 来源** |
| `faction_context.md` | 52 | `FactionService` | 3 个 | 文本 | 正常 |
| `rewrite_guardrails.md` | 45 | `pipeline_orchestrator._rewrite_with_guardrails` | 无 | 文本 | **需扩违规类型** |
| `optimize_dialogue.md` | 83 | `_run_optimizer[dialogue]` | 无 | JSON | **已按 additional_notes 对齐；仍缺差分输出** |
| `optimize_environment.md` | 89 | `_run_optimizer[environment]` | 无 | JSON | **已按 additional_notes 对齐；仍缺差分输出** |
| `optimize_psychology.md` | 132 | `_run_optimizer[psychology]` | 无 | JSON | **已按 additional_notes 对齐；仍缺差分输出** |
| `optimize_rhythm.md` | 133 | `_run_optimizer[rhythm]` | 无 | JSON | **已补输出纪律；仍缺差分输出** |
| `extraction.md` | 28 | `LLMService`（章节摘要） | 无 | 结构化 Markdown | 正常 |
| `import_analysis.md` | 73 | 导入旧小说 | 无 | JSON | 建议枚举动态注入 |
| `writer_persona.md` | 91 | **无代码引用（死档）** | 2 个 | 文本 | **需挂钩或删除** |
| `character_dna_guide.md` | 150 | **无代码引用（死档）** | 无 | 对话 + JSON | **需挂钩或删除** |

---

## 结语

当前系统工程脚手架是对的，但二次复核之后，关键缺口已经收敛成 **动态 token 预算 + 分场景生成 + 真正的模型 profile 分流 + 内容分级路由**。这四件做完，当前系统才算真正从"3000 字 demo"往"10000 字稳态连载"迈过去。

- 味道不会丢：味道本来就不是 jailbreak 给的，而是 `writing_v2` 硬约束 + `writer_persona` + `chapter_plan` 给的。需要做的是把 jailbreak 句子删掉、把 `writer_persona` 真接进生成链路、把评审 prompt 按 `content_rating` 分支。
- 18+ 能稳定写的前提是**路由层解决**，不是提示词花招。准备 1-2 个允许 explicit 的 provider/自部署模型，orchestrator 按项目分级路由即可；prompt 保持克制，只声明分级。
- 长篇稳定的前提是**卷级记忆 + 伏笔优先级 + 多 query RAG + prompt 指纹缓存 + 可断点续跑**。这五件必须一起上，缺一件都会在 100 章之后开始漂。

**落地第一步建议：** 从第五部分的"最小健壮改造 3 件"开始，P1 按业务节奏跟进。

---

## 附录 E 多分级（含 18+）落地前的对照实验方案

### E.1 实验目的

在正式改代码/改 prompt/切换 provider 之前，用对照实验验证三件事：

1. 现有 `concept.md` / `screenwriting.md` 里的 jailbreak 段落**是否真的帮到了 explicit 内容**，还是在拖累普通内容。
2. 删除 jailbreak 之后，**普通章节质量有无变化**（预期：持平或提升）。
3. 切换到 explicit 档 provider 之后，**18+ 场景质量有无明显提升**（预期：更自然、被拦率下降、元叙事消失）。

这套实验跑完你会有数据支撑决策，而不是凭感觉做改动。

### E.2 三个对照组

| 组 | Prompt | Provider/Model | 目的 |
|---|---|---|---|
| **A（基线）** | 当前 prompt（含 jailbreak 段） | 当前主力 API（你现在用的） | 复现现状 |
| **B（净化 prompt）** | 删除 jailbreak 段的 prompt | 当前主力 API（与 A 相同） | 验证 prompt 净化是否有副作用 |
| **C（分级路由）** | 删除 jailbreak 段的 prompt | 专门为 explicit 选的 provider/model | 验证路由方案的天花板 |

**关键约束：** A、B 只有 prompt 不同，其它一切相同；B、C 只有 provider 不同，其它一切相同。这样变量被隔离，结论才可信。

### E.3 测试集设计（5 个 scene，不用写完整章）

选 1 个已有项目（建议用 mature/explicit 定位的那种），人工选/写 **5 个 scene 级测试输入**，每个输入都是一条完整的 `chapter_plan.scene_list[i]` + 前一 scene 的 tail + 必要的角色信息。按尺度分布：

| Scene 编号 | 场景类型 | 目的 |
|---|---|---|
| S1 | 普通对话推进（两角色在茶馆交换情报） | 基础文笔基线 |
| S2 | 紧张冲突（主角被压制、暴力预警） | 验证 mature 级的压抑/暴力处理 |
| S3 | 心理戏 + 回忆闪回（童年创伤触发） | 验证技法不受 prompt 改动影响 |
| S4 | 情感张力场景（未逾矩的亲密预兆） | 验证 mature 级的"情欲张力"写法 |
| S5 | 明确亲密场景（explicit 级） | 验证 explicit 档的真实表达 |

每个 scene 在每个组下**各跑 3 次**（不同 seed/temperature 的自然波动需要多次采样），共 5 × 3 × 3 = 45 次生成。

### E.4 评分维度（统一 0-10 分，人工打）

| 维度 | 说明 | 对哪些 scene 关键 |
|---|---|---|
| D1 文笔 | 句式变化、感官描写、Show Don't Tell 到位度 | 全部 |
| D2 节奏 | 段落长度与情绪匹配、断章到位 | 全部 |
| D3 人物 | 行为/对话是否符合人设 DNA | 全部 |
| D4 尺度契合 | 场景目标的尺度是否真实呈现、还是被清洗/回避 | S2/S4/S5 |
| D5 稳定性 | 是否被拒答、是否出现"作为 AI 我不能"元叙事、是否字数严重不达标 | 全部 |
| D6 AI 腔 | 是否出现总结、套话、完美并列、俗套过渡 | 全部 |

每次生成给一个 6 维打分 + 一条"主要问题"备注。每组对每个 scene 取 3 次的均值 + 最小值（防止偶发爆表遮蔽稳定性问题）。

### E.5 实验操作步骤

**准备阶段（约 30 分钟）：**

1. **复制 prompt 备份：**
   ```bash
   cp backend/prompts/concept.md backend/prompts/concept.orig.md
   cp backend/prompts/screenwriting.md backend/prompts/screenwriting.orig.md
   ```
2. **准备 B 组 prompt：** 手动删除 `concept.md` 和 `screenwriting.md` 里的"创作限制"整段（包含"突破常规 AI 内容限制…情色…"的那一段），替换为第三部分 P0-7 给出的分级声明文本。保存为 `concept.clean.md` / `screenwriting.clean.md`。
3. **准备 C 组 provider：** 在 `backend/.env` 里加一组 explicit 专用的配置（或在代码侧临时硬编码一个 `LLM_PROFILE_EXPLICIT`），确认能跑通一次最小调用。
4. **准备测试输入：** 把 5 个 scene 写成 5 个 JSON 文件 `test_scenes/s1.json` ... `s5.json`，每个包含 `scene / pov / previous_tail / character_states / content_rating`。

**运行阶段（约 2-3 小时）：**

5. **A 组**：管理员后台把 prompt 库恢复为 `concept.orig.md` / `screenwriting.orig.md`；LLM profile 保持当前；5 × 3 次调用，结果存 `results/A/s{i}_r{j}.txt`。
6. **B 组**：管理员后台把 prompt 库替换为 `concept.clean.md` / `screenwriting.clean.md`；LLM profile 保持与 A 相同；再跑 5 × 3 次，存 `results/B/`。
7. **C 组**：prompt 库不变（仍是 B 组的 clean 版）；LLM profile 切到 explicit 专用；再跑 5 × 3 次，存 `results/C/`。
8. **盲评**：把 45 份输出随机化（去掉组号和 scene 标签，随机命名 `blind_001.txt ... blind_045.txt`），保留一份映射表 `blind_map.csv`。找 1-2 个人（或自己不看映射表）按 E.4 评分，填入 `results/scoring.csv`。
9. **去盲 + 聚合**：按 `blind_map.csv` 把分数还原到 A/B/C 分组，算每组每 scene 在每维度的均值 + 最小值。

**收尾阶段：**

10. 清理临时配置，恢复 prompt 库到实验前状态（或直接进入落地）。
11. 把 `results/scoring.csv` 和 `blind_map.csv` 归档，作为"改动前"的质量基线，以后每次大改都可以对比。

### E.6 结论判定规则

预期结果：

| 对比 | 预期 | 如实际不符怎么办 |
|---|---|---|
| B vs A（相同模型、净化 prompt） | 普通场景（S1/S2/S3）B 持平或优于 A；亲密场景（S4/S5）B 可能略降（因为主流 API 本来就在拦） | 若 B 在普通场景反而下滑，说明当前 jailbreak 词对这个模型有意外加成，需保留一个"中性版"引导词而非完全删除 |
| C vs B（净化 prompt、切 explicit 模型） | S4/S5 显著提升（D4 尺度契合 + D5 稳定性），S1/S2/S3 持平 | 若 C 在普通场景显著下滑，说明选的 explicit 模型文笔不够，需换另一个备选 |
| C vs A（净化 prompt + 切模型） | S4/S5 明显优于 A，S1/S2/S3 持平或优于 A | 若 C 在 S4/S5 上仍不如 A，说明路由目标模型没选对，回到模型调研 |

**放行标准（建议）：**

- B 组任何 scene 的 D5（稳定性）均值 ≥ 7 分，且相比 A 组 D1/D2/D3 不下滑超过 0.5 分 → 可以正式替换 prompt。
- C 组 S4/S5 的 D4 + D5 均值 ≥ 7 分，且 S1/S2/S3 不劣于 A → 可以上线分级路由，把 explicit 项目指向该 profile。
- 任何组出现"被拒答 / AI 元叙事"达 30% 以上 → 不合格，换模型或调 prompt 重来。

### E.7 只能在本地/测试环境做的提醒

- 实验期间 **不要改正式用户的项目数据**。建议在本地数据库或测试环境跑，至少专门为实验建一个 throwaway project。
- 45 次调用的账单预算建议单独审批（按当前主力模型估算：5 scene × 3 次 × 2 组 = 30 次主力模型调用 + 15 次 explicit 模型调用，约 $3-20 取决于模型选择）。
- 盲评环节的评分人**不要看 prompt 内容**，只看输出质量，避免先入为主。

---

## 附录 F 内容分级路由的落地顺序与回滚策略

> 附录 E 是"验证实验"，这一节是"上线操作"。必须按顺序做，顺序反了会出现一段"没有 jailbreak 也没有路由"的低谷期。

### F.1 上线顺序（单向依赖）

```
Step 1  在 LLMConfigService 加 profile 概念，但默认全走原 profile
        └─ 不破坏现有行为，可安全发布

Step 2  在 backend/.env.example 和生产配置加 explicit profile 的环境变量
        └─ 仍未启用，只是把配置先拉齐

Step 3  用附录 E 的 C 组方式，人工触发 explicit profile 的请求，确认可用
        └─ 这一步跑的是 throwaway project，不影响用户

Step 4  projects 表加 content_rating 字段（migration），默认 safe
        └─ 对所有历史项目填 safe，无行为变化

Step 5  新增 ContentRoutingService，按 content_rating → profile 映射
        └─ 此时还没有任何项目标记为 mature/explicit，路由逻辑走空转

Step 6  前端加"项目分级"的设置界面，允许作者为自己的项目改分级
        └─ 作者开始主动把项目调成 mature/explicit，路由开始生效

Step 7  最后才删除 concept.md / screenwriting.md 的 jailbreak 段落
        └─ 此时 explicit 项目已经被正确路由，不依赖 prompt 侧的 jailbreak
```

**反面教材：** 把 Step 7 提前到 Step 3 之前做，你会在没有 explicit 路由的情况下删掉了 jailbreak，所有 18+ 场景短期都会被主流 API 清洗或拒答。

### F.2 回滚策略

每一步都设计成可快速回滚：

| 步骤 | 回滚方法 | 恢复时间 |
|---|---|---|
| Step 1-2 | 配置回滚，重启服务 | < 2 分钟 |
| Step 3 | 本地实验，无需线上回滚 | — |
| Step 4 | `content_rating` 字段保留（无破坏性），只需把业务逻辑关闭 | < 5 分钟 |
| Step 5 | `ContentRoutingService` 加一个 feature flag `enable_content_routing`，关掉即回到原路由 | < 2 分钟 |
| Step 6 | 前端隐藏分级设置入口，后端仍返回 safe | < 5 分钟 |
| Step 7 | 恢复 `concept.md` / `screenwriting.md` 的 orig 版本，重新 seed 或 admin 后台直接改 DB content | < 10 分钟 |

**关键：** Step 5 的 feature flag 建议接管所有后续步骤。线上出任何问题，第一反应是关 flag，让路由回到"全部走默认 profile"。prompt 层面的回滚由 admin 后台完成，不需要发版。

### F.3 上线后的监控指标

路由上线后，至少监控两周以下指标，按 `content_rating` 分组：

- **请求成功率**：是否被 provider 拒答（空响应 / 错误码）。
- **字数达标率**：`len(output) >= min_word_count * 0.9` 的比例。
- **六维评审均分**：按分级分别统计，explicit 档不应显著低于 safe/mature。
- **用户重生成率**：作者对生成结果按 "重生" 按钮的比例（间接反映满意度）。
- **Token 成本**：按 profile 分组统计 prompt / completion token 总量。

三个异常信号需要立即回滚或介入：

1. explicit profile 的请求成功率 < 90%（provider 稳定性问题或配额不够）。
2. safe profile 的六维评审均分下滑 > 0.5（prompt 净化意外影响了普通场景）。
3. 任何 profile 出现 "作为 AI / 我不能" 类元叙事的比例 > 5%（路由规则没匹配对）。

### F.4 合规守线（任何分级都适用）

分级路由不是"给了 explicit 就啥都能写"，以下红线在所有分级下都强制守住，应该同时在 prompt 层（显式声明）和代码层（关键词黑名单 + 内容再次审核）双重拦截：

- 真实在世人物的露骨描写（名人、政治人物等）。
- 未成年人的任何亲密/性相关描写，包括模糊年龄设定下"看起来像成年但声明是未成年"的规避写法。
- 非自愿伤害未成年（虐待/拐卖/暴力）的煽动性或细节性描写。
- 具体的自残/自杀方法。
- 可能被用于真实犯罪的操作指南（毒品合成、武器制造等）。

这些不是"尺度问题"而是平台级合规问题，即使作者主动请求也要拒绝。建议在 `ContentRoutingService` 之前加一道轻量级的 `ContentPolicyGuard`，对章节大纲和导演脚本做预检查；预检查失败的章节不进入任何 profile 生成，直接返回作者侧报错。
