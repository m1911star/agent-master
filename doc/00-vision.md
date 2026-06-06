# 00 · 愿景与边界

> 状态：草案 · 含关键决策点（标 ⚠️），需要你确认

## 1. 是什么

**Agent Master 是一个 local-first 的多 agent 实时观测 + 协调控制台。**

把你电脑上跑着的所有 AI agent（Claude Code、Codex、Hermes、OpenCode、Pi、
自定义脚本、未来的 CrewAI / LangGraph / 任何 agent runtime）汇总到一块屏幕上，
让你：

- **看到** 每个 agent 当前在干什么（token 级延迟）
- **看懂** session 之间的依赖、谁 spawn 了谁、workflow 进展到哪一步
- **介入** 危险操作前的审批（本地弹窗或远程手机）
- **复盘** 任何一段历史执行（事件流 + 工件 + 决策点）

## 2. 不是什么（重要边界）

| 不是 | 为什么 |
|------|--------|
| 不是另一个 agent runtime | 你已经有 5 个了，问题是缺协调层不是缺执行层 |
| 不是 task manager / Jira 替代 | task 模型让 paperclip 做，我们做执行可见性 |
| 不是 prompt 编辑器 / agent 配置 IDE | Cursor / Continue 已经覆盖 |
| 不是 chatbot 前端 | OpenWebUI / LibreChat 已经覆盖 |
| 不是云端 SaaS | LangSmith / Langfuse / Helicone 已经覆盖 |

## 3. 三个核心痛点（你的原话）

1. **从 agent 任务的实时监测出发** — 不只是事后看 log，是实时
2. **看到不同类型 agent 的执行状态和输出** — 异构 agent 一致视图
3. **看到不同 session 之间的拓扑逻辑** — 谁 spawn 了谁，依赖关系
4. **需要权限及审批的时候，能在平台上做** — HITL，可远程
5. **能定义/可视化 workflow** — 用户手工 DAG 或 agent 自主生成的 DAG

## 4. 你没说但必然会需要的（按优先级）

| # | 功能 | 为什么需要 | 优先级 |
|---|------|-----------|--------|
| A | **成本追踪** | 5 个 agent 24/7 跑，不看就要爆 | P0 |
| B | **失败检测 + 死循环检测** | agent 卡死/重复劳动是你最大的隐性损失 | P0 |
| C | **跨 session context 复用** | A 干了 30 分钟，B 不该从零开始 | P1 |
| D | **artifact 沉淀** | 输出的文件/PR/draft 应该被索引 | P1 |
| E | **回放（time-travel）** | 拓扑+事件流自然支持，做 debug 用 | P1 |
| F | **行为模式分析** | 哪种 session 高产出？哪种烂尾？ | P2 |
| G | **多机器/分布式** | 笔记本+台式机，你已经提过 | P2 |
| H | **触发器** | 文件变化/cron/webhook 触发 agent | P2 |
| I | **Pixie 集成** | 它是事后统计层，能直接吃我们的事件流 | P2 |
| J | **Skills / playbook 沉淀** | 经验固化，下次少走弯路 | P3 |

## 5. 和 Paperclip 的关系（已决策 ✅）

`~/paperclip/` 是 **MIT 开源、生产级**的"AI 公司控制台"项目，由 Paperclip Labs 在做。
它覆盖了**编排层**你想要的大部分能力，但留了你最在意的两条空白。

### 决策：**独立产品 + 吸收 paperclip 精华**

不是 fork，不是 plugin，不是兼容层。Paperclip 对我们来说是：

- **设计参考库** — 它的对象模型（company/agent/issue/heartbeat/approval/budget）
  已经被生产验证，直接借鉴对象划分和字段设计
- **代码参考库（MIT 友好）** — 11 个 adapter 实现、approval 流程、budget 计算、
  audit log 模式，需要时直接抄过来改名
- **不是依赖** — 我们的二进制不依赖 paperclip 运行
- **不是替代** — 不打算把 paperclip 用户迁过来

### 我们的产品 = paperclip 编排能力 + token 级实时观测 + workflow 可视化

| 层 | 来源 | 我们的做法 |
|----|------|-----------|
| 编排层（org/task/budget/approval） | 借鉴 paperclip 对象模型 | 自己实现，简化字段，去掉 multi-company 等 V1 不需要的 |
| Adapter 接入层 | 参考 paperclip 11 个 adapter | 自己实现，扩展出"观测模式"（read-only 扒文件，不打扰 agent） |
| 实时观测层 | 我们的 spike 独有 | fsevents + sqlite WAL tail，1-1000ms 延迟 |
| Workflow DAG 可视化 | Paperclip 明确不做 | 自己实现，参考 LangGraph Studio / n8n |
| HITL 审批（含远程） | 借鉴 paperclip approval gates | 自己实现，加 LangGraph 风格 checkpoint/resume 让审批可挂起几小时 |

### Paperclip 边界（它显式不做的，正是我们的位置）

> Paperclip 官方原文：
> - "Not a workflow builder. No drag-and-drop pipelines."
> - "Do not lead with raw bash logs and transcripts."
> - "execution visibility without log worship"

它把这三块都留给了我们。

## 6. MVP 边界

按"观测层 → 编排层 → workflow 可视化 → HITL"的顺序推进。每阶段都是可用的产品，不是 demo。

### V0.1 · 观测层（夯实地基）
- 3 个 adapter：Claude Code / Codex / Hermes（read-only 模式，扒文件，不打扰 agent）
- 实时事件流：fsevents + sqlite WAL tail，L1 状态级实时（1-3 秒延迟）
- session 列表 + 单 session 实时视图（事件流 + 进度）
- 简单拓扑图：session 父子/sidechain（hermes 直接有，claude/codex 从 metadata 推）
- 本地 web UI（127.0.0.1，仅观测，不写）

**交付物：** "spike 的产品化版本"。任何用户装上就能看自己的 5 个 agent 在干什么。

### V0.2 · 编排层（吸收 paperclip 精华）
- 引入 `task` 对象（借鉴 paperclip issue 模型，简化字段）
- 引入 `agent` 注册（不只是被动观测，可以"主动派活"）
- 引入 `budget`（token/cost 上限，超限暂停 agent）
- write-mode adapter：能给 agent 派 task、传 prompt、收回 result
- audit log（mutation 全留痕）

**交付物：** 你能从 UI 上点 "Claude 干这个、Codex 干那个"，看着它们并行跑、不爆预算。

### V0.3 · Workflow 可视化（独门武器）
- DAG 数据结构（节点 = task，边 = 依赖）
- 自动从执行历史推 DAG（agent A 调用了 agent B → 一条边）
- 手工编辑器（可选，DSL 优先，可视化第二）
- 回放（time-travel）

**交付物：** 你能看到 workflow 怎么自然演化出来，并 fork 一份保存为 template。

### V0.4 · HITL + 远程
- approval gate（agent 在某节点停下来等批准）
- LangGraph 风格 checkpoint（agent 可挂起几小时等审批）
- 手机端审批入口（tailscale / cloudflare tunnel）
- pattern 规则（一次审批可生成"以后这类自动通过"的规则）

**交付物：** 你出门买菜，agent 跑到危险操作就停，你在手机上一键批准或拒绝。

### V0.5+（不做承诺，按需）
- artifact 索引（agent 输出的文件/PR/draft）
- 多机器同步
- Pixie 集成（事后统计层吃我们的事件流）
- 跨 session context 复用
- 死循环检测 / 行为模式分析

## 7. 一句话愿景

> 你坐在屏幕前，五个 agent 在并发干活。
> 一眼扫过去：谁在跑、跑到哪、卡住没、要不要批。
> 这就是 Agent Master。
