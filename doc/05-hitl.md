# 05 · HITL 审批系统

> 状态：草案 · V0.4 落地，但接口要在 V0.1 数据模型里预留

## 设计目标

**让 agent 在危险操作前停下，等待人类决策（本地弹窗或远程手机），决策完无缝继续。**

核心特征：
1. **可挂起几小时** — 你出门买菜，agent 等你
2. **可远程批准** — 手机一键，不需要回电脑
3. **可批量授信** — 一次决策可生成"以后这类自动通过"的规则
4. **可降级** — 审批超时不死锁，按预设策略走

## 借鉴自三家的核心机制

| 来源 | 借鉴什么 | 用在哪 |
|------|---------|--------|
| **LangGraph interrupt()** | checkpoint + resume + thread_id | 让 approval 可挂起任意时长 |
| **Claude Code permissions** | `allow/deny/ask` pattern 数组 | 批一次生成永久规则 |
| **Codex CLI approval modes** | 提权 + 沙箱降级耦合 | 审批超时降级到沙箱执行 |

详细对比见前期调研，已写进 00-vision.md。

---

## 三种触发路径

### 路径 A · Hook 触发（推荐）

agent 自己调 hook 通知我们：

```
agent ─(PreToolUse)─→ hook 脚本 ─(HTTP POST)─→ core
                                                  │
                                                  ▼
                                            创建 Approval
                                                  │
                                                  ▼
                                         通知 UI / 推手机
                                                  │
                                          (用户决策)
                                                  │
                                                  ▼
                                          hook 收到响应
                                                  │
                                                  ▼
                                            exit 0/2
                                                  │
                                                  ▼
                                         agent 继续/拒绝
```

**适用：** Claude Code（PreToolUse hook 原生支持）、Codex（hook 在 V0.2 接入）

**优点：** agent 进程自然阻塞，state 由 agent 自己持有。

**缺点：** 进程一直占着，多 approval 并发要占很多进程槽位。

### 路径 B · Proxy 拦截

我们 mitm 拦截 agent 到 LLM 的 HTTP 调用，解析出 tool_use，按规则放行/拦截。

```
agent ─→ LLM API ─→ ...
         ↑
   [proxy] 看到 tool_use 就拦下
         │
         ▼
    创建 Approval
         │
         ▼
    用户决策后
         │
         ▼
    透传 / 修改 / 阻断
```

**适用：** 不支持 hook 的 agent（如自定义脚本、CrewAI/AutoGen）

**优点：** agent 无感知，零接入成本

**缺点：** 实现复杂，HTTPS 证书管理麻烦

V0.4 先做路径 A，路径 B 推迟到 V0.5+。

### 路径 C · Checkpoint + Resume（远程审批专用）

借鉴 LangGraph，agent 自己实现 checkpoint：

```python
# agent 代码里
result = agent_master_client.checkpoint(
    name="dangerous_op",
    detail={"command": "rm -rf /tmp/x", "diff": "..."},
    timeout_minutes=240,
    default_action="reject",
)
if result.approved:
    actually_do_it()
```

`checkpoint()` 调用：
1. 把 agent state 序列化到 Approval.checkpoint_data
2. 退出当前进程（释放资源）
3. 收到决策后，新进程从 checkpoint 恢复

**适用：** 我们自己的 agent / 改造过的 agent

**优点：** 真正可挂几小时（进程都没了，零资源）

**缺点：** agent 必须配合改造

V0.4 我们的 hermes 先做这条，外部 agent 走路径 A。

---

## 数据流转完整时序

```
[t=0]     agent 触发 hook
[t=0.01]  hook POST 到 core /api/approve
[t=0.02]  core 创建 Approval(pending)，写 DB
[t=0.02]  core fanout：SSE 推 UI，APNs 推手机
[t=0.05]  UI 弹窗 / 手机震动
[t=600]   用户在手机点 approve
[t=600.1] 决策写回 DB
[t=600.1] 通知所有等候端：hook 收到 200 + 决策
[t=600.2] hook exit 0
[t=600.2] agent 继续

如果有 rule_pattern：
[t=600.1] 同时创建 Rule，写入 DB
[t=next]  下次同类操作直接查 Rule，秒过
```

---

## Pattern 规则系统（借鉴 Claude Code）

### 规则数据

```
Rule {
  id: uuid
  agent_id: uuid?           # null = 全局
  scope: text               # "tool:bash" / "file:**/.env" / "url:https://api.openai.com/*"
  match_kind: enum          # exact / glob / regex
  action: enum              # allow / deny / ask
  ttl: enum                 # session_only / 24h / permanent
  expires_at: timestamp?
  created_from_approval_id: uuid?
  created_at: timestamp
  reason: text?             # 用户备注"为什么这样配置"
}
```

### 决策流程

```python
def decide(approval_request):
    # 1. 查 deny 规则（最高优先级，先拒后允）
    if rules.match(approval_request, action="deny"):
        return Decision.REJECT

    # 2. 查 allow 规则
    if rules.match(approval_request, action="allow"):
        return Decision.AUTO_APPROVE

    # 3. 查 ask 规则（强制要 ask，覆盖默认 auto）
    if rules.match(approval_request, action="ask"):
        return Decision.WAIT_USER

    # 4. fallback 默认策略
    return agent.default_approval_policy
```

### 规则模板预设

UI 应提供常见预设，一键应用：

| 预设 | 规则 | 说明 |
|------|------|------|
| 安全默认 | deny `rm -rf /*` `git push --force` `npm publish` | 永远不允许 |
| 信任 git 读 | allow `git status` `git log` `git diff` | 常见无害 |
| 信任 npm 测试 | allow `npm test*` `npm run test*` | 测试无害 |
| 危险 ask | ask `rm` `git push` `*delete*` | 强制确认 |

---

## 远程审批通道

### V0.4 三档实现

| 档位 | 实现 | 体验 |
|------|------|------|
| **本地** | 浏览器 + 系统通知 | 0 配置 |
| **局域网** | Bonjour 广播 + 手机网页 | 5 分钟配置 |
| **跨网** | Tailscale | 15 分钟配置 |

### 手机端 UI 关键要素

1. **必须能看到 diff** — 否则用户判断不了
2. **三个按钮**：批准 / 拒绝 / 批准并加入规则
3. **5 秒反悔机制** — 防误触
4. **通知摘要** — 推送内的内容要够，扫一眼能决策的就不开 app

### Push 选型

| 选项 | 优点 | 缺点 | 决策 |
|------|------|------|------|
| Pushover | 简单 | $5 一次 | V0.4 默认 |
| Bark (iOS) | 免费、开源 | iOS only | 备选 |
| Telegram bot | 跨平台 | 要 bot 管理 | 备选 |
| 自建 APNs | 完全自主 | 证书烦 | V0.6+ |
| ntfy.sh | 开源自托管 | 跨平台 | ✅ V0.4 推荐 |

---

## 死锁防护

### Approval 永远不会卡死，因为：

1. **timeout** — 默认 30 分钟，超时按 `default_action` 走
2. **default_action** — 可选 reject / approve / pause（pause 把整个 agent 暂停，等手动激活）
3. **降级到沙箱** — V0.5 选项：超时不直接拒绝，而是降级到只读沙箱继续跑

### 用户离线策略

```yaml
# 配置在 agent 上
approval_policy:
  default_timeout: 30m
  on_timeout: reject       # 安全保守

  # 危险操作专用
  dangerous_ops:
    pattern: ["rm", "push", "publish"]
    timeout: 4h            # 给用户更多时间
    on_timeout: pause      # 不杀进程，让用户回来手动决策
```

---

## UI / UX 设计

### 桌面弹窗

```
┌─────────────────────────────────────────┐
│ ⚠️ Claude Code wants to: run command    │
├─────────────────────────────────────────┤
│ $ rm -rf node_modules                   │
│                                         │
│ Working dir: ~/sideproject/agent-master │
│ Risk: file deletion (high)              │
│                                         │
│ [ Approve ] [ Reject ] [ Approve + Rule ]│
│                                         │
│ Auto-reject in 28:34                    │
└─────────────────────────────────────────┘
```

### 待审列表

```
Pending Approvals (3)
├─ Claude Code · rm node_modules · 2m ago   [Approve][Reject]
├─ Codex · git push -f · 5m ago             [Approve][Reject]
└─ Hermes · curl https://... · 8m ago       [Approve][Reject]
```

### 批量决策

用户可勾选多个，一键 approve / reject。

### Rules 管理页

```
Active Rules (12)
├─ allow `npm test*` · permanent · created 2 days ago [Edit][Delete]
├─ deny `git push --force` · permanent                [Edit][Delete]
├─ ask `rm *` · permanent                             [Edit][Delete]
└─ ...
```

---

## V0.1 准备工作（不实现 HITL，但留 hook）

V0.1 必须做的事情：

1. **Approval 表 schema** 上线（即使没有写入逻辑）
2. **Rule 表 schema** 上线
3. **`/api/approve` 端点骨架** 上线（返回 not_implemented）

这样 V0.4 不用 schema migration，平滑升级。

---

## 不做（V0.4 范围内）

- Proxy 拦截（路径 B 推迟）
- 多人协作审批（一人就够）
- Approval 撤销（决策后无法 revert）
- 复杂条件规则（"只在工作日 9-18 点 allow X"，YAGNI）

---

下一份 06-architecture.md 讲整体系统怎么搭。
