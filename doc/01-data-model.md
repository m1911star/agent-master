# 01 · 核心数据模型

> 状态：草案 · 这是整套设计的地基，所有后续文档都引用这里的对象定义

## 设计哲学

1. **借鉴 paperclip 的对象划分** — 它的 90 个表里，核心其实就 7-8 个对象，其他都是
   multi-company / RBAC / secrets / plugin 等企业级延伸。我们 local-first 单用户，
   只要核心
2. **简化字段，但不简化关系** — 字段能砍就砍，但对象之间的关系结构忠实保留
3. **加 paperclip 没有的两个**：`Event`（高频事件流）和 `Workflow`（DAG 模板）

## 七个核心对象

```
Agent ─┬─ Session ─┬─ Run ─┬─ Event[]
       │           │       ├─ Approval[]
       │           │       └─ Artifact[]
       │           └─ Task ┘
       └─ Budget（按 agent / task 配额）
       └─ Workflow（V0.3 才加）
```

---

### 1. Agent

> 一个"可以接活的实体"。等同于 paperclip 的 `agents` 表。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| name | text | 用户起的名字，如 "Claude on macbook" |
| adapter_type | text | 必须匹配已注册 adapter，如 "claude_code" |
| adapter_config | jsonb | adapter 特定配置（workdir、model 等） |
| status | enum | `idle` / `busy` / `paused` / `error` / `offline` |
| capabilities | text[] | 如 `["code", "review", "research"]`，用于 task 路由 |
| budget_id | uuid? | 关联到 Budget（可选） |
| created_at | timestamp | |
| updated_at | timestamp | |

**状态机：**
```
idle ──(接到 task)──→ busy
busy ──(完成)──→ idle
busy ──(出错)──→ error
任何 ──(用户暂停)──→ paused
任何 ──(进程死了)──→ offline
```

**与 paperclip 差异：** 砍掉 `role` / `title` / `reporting_lines`（org chart）等
V0.2 不需要的字段，V0.3 编排层时再补。

---

### 2. Session

> 一次"持续的工作时段"，属于一个 agent。等同于一个 claude code 进程从启动到退出，
> 或一个 hermes session 的生命周期。

**Paperclip 没有完全对应** — paperclip 是 task-driven 的，没有"长会话"概念。
我们要观测，必须有 session。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| agent_id | uuid | fk → Agent |
| external_id | text | adapter 报告的 ID（如 claude code 的 sessionId） |
| parent_session_id | uuid? | 父 session（如 sidechain） |
| workdir | text | 工作目录 |
| started_at | timestamp | |
| ended_at | timestamp? | null = 还活着 |
| last_active_at | timestamp | 最近一次事件时间 |
| status | enum | `active` / `idle` / `closed` |
| summary | text? | 一句话总结，由 agent 或 LLM 生成 |
| meta | jsonb | adapter 特定的元数据 |

**关键索引：**
- `(agent_id, status, last_active_at desc)` — 首页查"活跃 session"
- `(external_id)` — 反查 raw 文件到 session
- `(parent_session_id)` — 构建拓扑

---

### 3. Run

> 一次"执行批次"。一个 session 内可以有多个 run。Run 是和 task 对应的最小单位。

借鉴 paperclip 的 `heartbeat_runs`。Run 解决一个关键问题：
**同一个 claude 会话里你发了 3 个不同的任务，应该是 3 个 run。**

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| session_id | uuid | fk → Session |
| task_id | uuid? | fk → Task（可选） |
| trigger | enum | `manual` / `heartbeat` / `cron` / `webhook` / `spawn` |
| started_at | timestamp | |
| ended_at | timestamp? | |
| status | enum | `pending` / `running` / `success` / `failed` / `interrupted` |
| exit_reason | enum? | `completed` / `error` / `approval_pending` / `budget_exceeded` / `user_cancelled` |
| tokens_in | int | |
| tokens_out | int | |
| cost_usd | numeric(10,4) | |
| summary | text? | |
| error_message | text? | |

**为什么 Session 和 Run 分两层：**
- Session = agent 进程级连续性（长寿）
- Run = 任务级执行（短寿）
- 一个 session N 个 run，一个 task 可能跨多个 run（agent 中断后续跑）

---

### 4. Event

> 一个原子事件。高频写入。事件流的基本单位。

字段几乎直接抄自 paperclip 的 `heartbeat_run_events`（说明这套设计他们也是这么想的）：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | bigserial | 单调递增主键（高频） |
| run_id | uuid | fk → Run |
| seq | int | run 内单调递增（顺序保证） |
| ts | timestamp | 事件时间（adapter 报告，可能略早于 created_at） |
| created_at | timestamp | 写入时间 |
| kind | enum | 标准化事件类型（见下表） |
| stream | text? | `stdout` / `stderr` / `agent` / `tool` |
| level | text? | `info` / `warn` / `error` |
| color | text? | adapter 提示的展示色（如 paperclip 的 `color` 字段） |
| text | text? | 人类可读摘要（UI 优先用） |
| payload | jsonb | adapter 原始数据（debug 用） |

**标准 `kind` 枚举（V0.1 锁定）：**
```
user_message        — 用户输入
assistant_message   — agent 文字输出
reasoning           — 思考/内省（claude thinking、codex reasoning）
tool_call           — 工具调用开始
tool_result         — 工具调用结果
status_change       — agent 状态变化
approval_requested  — 触发审批
approval_decided    — 审批结果
artifact_created    — 产出工件
error               — 错误
session_start       — session 开始
session_end         — session 结束
run_start
run_end
```

新 adapter 报告的事件如果不在标准 kind 里，统一塞 `kind = "raw"` + `payload`，
后续 UI fallback 渲染。

**关键索引：**
- `(run_id, seq)` — 按顺序读事件流
- `(run_id, kind)` — 筛选某类事件
- `(ts)` — 时间轴查询

---

### 5. Task

> 一项工作。借鉴 paperclip 的 `issues` 表，但**大幅简化**。
> V0.1 暂不引入，V0.2 编排层才加。先把字段定义好。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| title | text | |
| description | text | 完整任务描述 |
| parent_task_id | uuid? | fk → Task，构成树 |
| assignee_agent_id | uuid? | fk → Agent |
| status | enum | `pending` / `in_progress` / `blocked` / `completed` / `cancelled` |
| created_by | text | `user` 或 `agent:<uuid>` |
| priority | int | 0-100 |
| goal_chain | text[] | 借鉴 paperclip "goal ancestry"，向上的目标链 |
| created_at | timestamp | |
| started_at | timestamp? | |
| completed_at | timestamp? | |

**为什么砍掉 paperclip 这么多字段：**
- ❌ `company_id` — 单用户，不需要
- ❌ `project_id` — 用 `parent_task_id` 实现层级
- ❌ `blockers` / `relations` — V0.3 需要时再加
- ❌ `documents` / `attachments` / `comments` — 都进 Artifact 或 Event
- ❌ `labels` / `inbox_state` — 不是 V0.2 阻塞项

---

### 6. Approval

> 一个等待人类决策的关卡。V0.4 才加。先定义。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| run_id | uuid | fk → Run |
| agent_id | uuid | fk → Agent |
| requested_at | timestamp | |
| decided_at | timestamp? | null = 还在等 |
| expires_at | timestamp? | 默认 30 分钟，超时按 `default_action` 处理 |
| status | enum | `pending` / `approved` / `rejected` / `expired` / `auto_approved` |
| default_action | enum | 超时默认：`reject` / `approve` / `pause` |
| subject | text | 一句话主题，如 "npm install foo 是否允许？" |
| detail | jsonb | diff / 命令 / 参数 / 完整上下文 |
| decision_by | text? | `user:<id>` 或 `rule:<id>` |
| decision_reason | text? | |
| rule_created_id | uuid? | 是否同时生成了 pattern 规则 |
| checkpoint_data | jsonb? | agent 等待时的 state（LangGraph 风格） |

**关键设计：** `checkpoint_data` 让 agent 可以挂起几小时（手机上批），
是远程审批必备字段，详见 05-hitl.md。

---

### 7. Artifact

> agent 产出的工件。任何"完成了某件事"的具象证据。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | uuid | 主键 |
| run_id | uuid | fk → Run |
| kind | enum | `file` / `pr` / `commit` / `document` / `screenshot` / `url` / `json` |
| title | text | 用户可读的名字 |
| path | text? | 本地路径或 URL |
| content_hash | text? | 内容指纹（dedup 用） |
| size_bytes | int? | |
| created_at | timestamp | |
| meta | jsonb | kind 特定的元数据（PR url、commit sha 等） |

**为什么 artifact 是一等公民：**
- 让"agent 真的做完事"可被验证（参考 paperclip "Output-first" 原则）
- 后续可以做 artifact 索引、跨 session 引用、回放时展示

---

## 配套对象（次要，先列字段不展开）

### Budget
```
id, scope (agent | task | global), period (day | month | total),
limit_tokens, limit_usd, spent_tokens, spent_usd,
on_exceed (pause | warn | stop)
```

### Rule（V0.4 HITL）
```
id, agent_id?, pattern, action (allow | deny | ask),
scope (session | permanent), expires_at?,
created_from_approval_id?
```

### Workflow（V0.3）
```
id, name, definition_yaml, version, created_at
```

### AuditLog
```
id, ts, actor (user | agent | system), action,
target_type, target_id, payload, ip_addr?
```

---

## 关系约束（数据库层强制）

```sql
Session.agent_id          → Agent.id           (CASCADE on agent delete)
Session.parent_session_id → Session.id         (SET NULL)
Run.session_id            → Session.id         (CASCADE)
Run.task_id               → Task.id            (SET NULL)
Event.run_id              → Run.id             (CASCADE)
Approval.run_id           → Run.id             (CASCADE)
Approval.agent_id         → Agent.id           (CASCADE)
Approval.rule_created_id  → Rule.id            (SET NULL)
Artifact.run_id           → Run.id             (CASCADE)
Task.parent_task_id       → Task.id            (SET NULL)
Task.assignee_agent_id    → Agent.id           (SET NULL)
```

CASCADE = 删 agent 时整个观测历史都没了，符合预期（local-first，用户决定）。
SET NULL = 软引用，对象消失但其他引用方能继续工作。

---

## 命名约定

- 表名：`snake_case` 复数（`agents`, `sessions`, `runs`, ...）
- 字段名：`snake_case`
- 主键：`id`（uuid v7，含时间戳，自然按时间排序）
- 时间字段：`*_at`
- 外键：`<table_singular>_id`
- 枚举字段：text，应用层校验（避免 DB 迁移痛苦）

---

## 不在数据模型里（但需要）

- **Adapter 定义** — 在代码里（`packages/adapters/<name>/`），不在 DB
- **UI 偏好** — V0.2 加 `ui_preferences` 表
- **Secrets** — V0.5 加 secret store（V0.1 用 ENV）
- **多机器同步状态** — V0.5

---

## 与 paperclip 表对应总表

| 我们的对象 | Paperclip 表 | 取舍 |
|----------|-------------|------|
| Agent | `agents` | 砍掉 org chart 字段 |
| Session | (无) | 我们新增 |
| Run | `heartbeat_runs` | 字段对应 |
| Event | `heartbeat_run_events` | 字段对应 |
| Task | `issues` + `goals` | 大幅简化 |
| Approval | `approvals` + `approval_comments` | 加 `checkpoint_data` |
| Artifact | `issue_work_products` + `issue_attachments` | 合并简化 |
| Budget | `budget_policies` + `budget_incidents` | 简化 |
| Rule | (无) | 我们新增（Claude Code 风格 pattern） |
| Workflow | (无) | 我们新增 |
| AuditLog | `activity_log` | 字段对应 |

7 个核心 + 4 个配套 = 11 个对象。比 paperclip 的 90 个表少了一个数量级，
但保留了所有真正核心的设计。

---

下一份 02-adapter.md 会详细讲："这些标准对象怎么从异构 agent 里被抽取出来。"
