# 04 · 拓扑与 Workflow 可视化

> 状态：草案 · paperclip 明确不做的那块，是我们的独门武器

## 两种"拓扑"，别混

| 类型 | 含义 | 数据来源 | 例子 |
|------|------|---------|------|
| **执行拓扑** | 实际跑出来的 session/run 关系图 | 事件流 + parent_session_id | "Claude 派出了 3 个 Codex 子任务" |
| **Workflow 模板** | 用户/agent 定义的 DAG | 用户写 YAML / agent 生成 | "前端 → 后端 → 测试 → 部署" |

V0.1 先做执行拓扑（自动推导，零用户配置）。
V0.3 加 Workflow 模板（手工或自动生成 DAG）。

---

## 执行拓扑：数据结构

```
SessionNode {
  id: uuid
  agent_id: uuid
  status: active | idle | closed
  summary: text
  started_at: timestamp
  ended_at: timestamp?
  parent_session_id: uuid?  ← 拓扑的边
}

RunNode {
  id: uuid
  session_id: uuid
  task_id: uuid?
  status: ...
  triggered_by_run_id: uuid?  ← run 之间的因果链
}
```

边的来源：

1. **session 父子** — `parent_session_id`（hermes 直接有；claude code 的 sidechain 也有）
2. **run 派生** — agent A 的某个 run 触发了 agent B 的新 run（V0.2+ 才有，需要 Controller 记录）
3. **task 树** — `parent_task_id`（V0.2+）

### 推导算法

```python
def build_topology(time_window: timedelta) -> Graph:
    sessions = db.get_active_sessions(within=time_window)
    nodes = [SessionNode.from_db(s) for s in sessions]

    edges = []
    for s in sessions:
        if s.parent_session_id:
            edges.append(Edge(s.parent_session_id, s.id, kind="parent"))

    # V0.2+: run 派生
    for run in db.get_runs_with_trigger("spawn"):
        edges.append(Edge(run.triggered_by_run_id, run.id, kind="spawn"))

    return Graph(nodes, edges)
```

时间窗口默认 24 小时（避免图爆炸）。

---

## 执行拓扑：UI 表达

借鉴 paperclip 没做、但 LangGraph Studio / Temporal UI 做对的地方：

### 视图 1 · 实时全景

```
┌─────────────────────────────────────────────┐
│ [Claude-1] ────► [Codex-2]                  │
│   │              │                          │
│   ▼              ▼                          │
│ [Codex-1]      [Hermes-1]                   │
│   │                                         │
│   ▼                                         │
│ [Hermes-2]                                  │
└─────────────────────────────────────────────┘
节点颜色 = 状态：绿=active 灰=idle 红=error 黄=approval_pending
节点大小 = 累计 token（让重 agent 一眼看出）
边粗细 = 派活次数
```

### 视图 2 · Sequence View（横向时间轴）

```
Claude-1  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
              │
              ▼ spawn
Codex-1            ━━━━━━━━━━━━━━━━
                       │
                       ▼ approval
[user batch approve]      ●
                            │
Hermes-1                          ━━━━━━━━━━
                                       │
                                       ▼ done
```

借鉴 Temporal UI 的 timeline view。debug 时极有用。

### 视图 3 · 树形（适合多层 sidechain）

```
└─ Claude-1 [active]
   ├─ Codex-1 [spawned, active]
   │  └─ Hermes-1 [spawned, idle]
   └─ Codex-2 [spawned, closed]
```

适合 hermes 那种深嵌套场景。

---

## 选型：图渲染库

| 候选 | 优点 | 缺点 | 决策 |
|------|------|------|------|
| **React Flow** | 生态最大、文档好、性能足 | bundle 大 | ✅ 主选 |
| **Cytoscape.js** | 老牌、布局算法多 | API 老旧 | ❌ |
| **vis-network** | 简单 | 性能差 | ❌ |
| **D3 hand-roll** | 极致自由 | 工作量爆 | ❌ |
| **Dagre + canvas** | 性能好 | 自己实现交互 | 备选 |

V0.1 用 React Flow，遇到性能瓶颈再考虑 canvas 重写。

---

## Workflow 模板（V0.3）

到 V0.3 才做。这里先定义 DSL，让数据模型有锚点。

### DSL 形态

**Workflow 优先 YAML，可视化是查看用，编辑可选。**

理由：
1. agent 自动生成 workflow，YAML 比 JSON-for-canvas 容易生成
2. 用户可以 git diff workflow 改动
3. 复杂逻辑（条件分支、循环）DSL 比拖拽好表达

```yaml
# .agent-master/workflows/feature-dev.yml
name: feature_development
description: 从 issue 到 PR 的标准流程
version: 1

inputs:
  - name: issue_url
    type: url
  - name: branch
    type: string
    default: "feat/${issue.title|slug}"

nodes:
  - id: research
    agent: claude_code
    prompt: "读 issue ${issue_url}，给出实现 plan"
    output: plan.md

  - id: implement
    agent: claude_code
    depends_on: [research]
    prompt: "按 ${research.plan} 实现，分支 ${branch}"
    output: commit_sha

  - id: review
    agent: codex
    depends_on: [implement]
    prompt: "审 ${implement.commit_sha}"
    approve_on_fail: true   # 失败需要用户确认

  - id: pr
    agent: hermes
    depends_on: [review]
    when: "${review.passed}"
    prompt: "开 PR for ${branch}"
```

### 数据模型补充

```
Workflow {
  id, name, version, definition_yaml,
  created_at, created_by
}

WorkflowRun {
  id, workflow_id, status,
  started_at, ended_at, inputs (jsonb)
}

WorkflowNodeRun {
  id, workflow_run_id, node_id,
  run_id?,    ← 关联到实际的 Run（agent 跑的那个）
  status, started_at, ended_at,
  output (jsonb)
}
```

### 自动从历史推 workflow

V0.3 的甜点功能：

```python
def suggest_workflow_from_history(session_ids: list[str]) -> str:
    """从一系列 session 的执行历史，反推一个 workflow 模板"""
    sessions = db.get_sessions(session_ids)
    edges = extract_topology(sessions)

    # 找出公共模式：哪些 agent 经常按什么顺序被调用
    nodes = []
    for s in topologically_sorted(sessions):
        nodes.append({
            "id": s.agent.name + "_" + s.summary[:10],
            "agent": s.agent.adapter_type,
            "prompt": extract_prompt_template(s),  # 抽出参数化版本
            "depends_on": find_parents(s, edges),
        })

    return yaml.dump({"name": "auto_workflow", "nodes": nodes})
```

→ 用户点 "save as workflow"，下次能复用。

---

## 触发器（V0.2 简化版 / V0.3 完整版）

Workflow 需要触发器：

| 类型 | V0.2 | V0.3 | 实现 |
|------|------|------|------|
| 手动 | ✅ | ✅ | UI 上点 Run |
| Cron | ✅ | ✅ | 用 hermes cron 或自己写 |
| 文件变化 | ❌ | ✅ | 复用我们的 fsevents 基建 |
| Webhook | ❌ | ✅ | HTTP 端点 |
| Agent 调用 | ❌ | ✅ | 给 agent 一个 `trigger_workflow` 工具 |

---

## "Agent 自主生成的 workflow"具体怎么做

V0.3 后期。流程：

1. 用户在 chat 里说"帮我把 issue X 全流程跑完"
2. Planner agent（可以是 Claude / Codex）生成 yaml 草稿
3. UI 渲染 DAG，用户审一遍
4. 一键 run，每个节点的实际执行还是各家 agent
5. 完成后可以 "save as template" 转成正式 workflow

这一步对 prompt 工程要求高，可能要等 V0.4 才稳。**V0.3 先做手动定义。**

---

## 关键交互细节

### 点击节点 → 钻取到 session 详情

拓扑视图是入口，不是详情。点击节点应该跳到该 session 的事件流视图（实时 stream）。

### 实时 vs 历史的切换

- 默认实时：拓扑图持续更新
- 选时间范围 → 进入历史模式，可拖时间轴回放（time-travel）

### 大图怎么办

10+ session 同时活跃时图会糊：
- 按 workdir / project 自动分组（折叠）
- mini-map 导航
- 搜索/过滤（按 agent / status / 时间）

---

## 不做（V0.1）

- 完整的 workflow 编辑器（V0.3）
- 图的自动布局美化（先用 React Flow 默认 Dagre）
- 拓扑 diff（对比两次 workflow run 的差异，V0.4）

---

下一份 05-hitl.md 讲审批和远程批准。
