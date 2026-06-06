# 02 · Adapter 协议

> 状态：草案 · 第三方接入新 agent 的唯一入口

## 设计目标

**让任何 agent 接入只需写一个 ~200 行的 adapter，不需要改 core。**

## Adapter 的三种能力

每个 adapter 可以实现 1-3 种能力，按需组合：

| 能力 | 必需性 | 说明 |
|------|--------|------|
| **Observer** | 必需 | 把 agent 的运行数据映射到我们的标准对象 |
| **Controller** | V0.2 可选 | 能给 agent 派活、暂停、终止 |
| **Approver** | V0.4 可选 | 能在 agent 危险操作前拦截 |

V0.1 我们做 3 个 Observer-only adapter（Claude Code / Codex / Hermes）。
后面阶段逐步加 Controller 和 Approver。

---

## Observer 协议

最小契约：把 agent 的私有数据格式翻译成我们的 7 个对象。

### 实现形式

每个 adapter 是一个独立的子进程或线程，源代码放在：
```
packages/adapters/<name>/
├── adapter.toml          # 元数据 + 能力声明
├── observe.py            # Observer 入口
├── control.py            # Controller 入口（V0.2+）
└── approve.py            # Approver 入口（V0.4+）
```

### `adapter.toml` 示例

```toml
[adapter]
name = "claude_code"
version = "0.1.0"
display_name = "Claude Code"

[capabilities]
observer = true
controller = false
approver = false

[observer]
# 告诉 core 这个 adapter 要监听什么
watch_paths = ["~/.claude/projects/**/*.jsonl"]
watch_kind = "fsevents"
poll_interval_ms = 1000  # fallback

[observer.session_detection]
# 从文件路径或内容怎么识别 session
strategy = "file_per_session"  # 一个文件 = 一个 session
external_id_extract = "filename_stem"
parent_id_extract = "$.sidechain.parent_id"  # JSONPath
```

### Observer 接口（Python 示例）

```python
class Observer:
    def list_existing_sessions(self) -> list[SessionDescriptor]:
        """启动时调用，扫一遍现有的所有 session"""

    def subscribe(self, callback: Callable[[Event], None]) -> Subscription:
        """订阅新事件流"""

    def parse_session(self, raw_file: Path) -> tuple[Session, list[Run], list[Event]]:
        """解析单个 session 的全部历史"""

    def map_event(self, raw: dict) -> Event | None:
        """把原始事件 dict 映射到我们的标准 Event 对象，返回 None 表示忽略"""
```

### Event 映射示例（Claude Code）

Claude Code 的 JSONL 每行长这样：

```jsonc
{"type": "user", "message": {"role": "user", "content": "..."}, "sessionId": "abc"}
{"type": "assistant", "message": {"content": [{"type":"thinking", ...}, {"type":"tool_use", ...}]}}
{"type": "result", "tool_use_id": "...", "content": [...]}
```

Adapter 把它映射成：

```python
def map_event(raw: dict) -> Event | None:
    t = raw.get("type")

    if t == "user":
        return Event(kind="user_message", text=raw["message"]["content"], payload=raw)

    if t == "assistant":
        msg = raw["message"]
        for block in msg.get("content", []):
            if block["type"] == "thinking":
                yield Event(kind="reasoning", text=block["thinking"], payload=block)
            elif block["type"] == "tool_use":
                yield Event(kind="tool_call", text=f"{block['name']}(...)",
                            payload={"tool": block["name"], "input": block["input"]})

    if t == "result":
        return Event(kind="tool_result", payload=raw)

    # ... 其他类型
    return Event(kind="raw", payload=raw)  # fallback
```

---

## Controller 协议（V0.2）

让 core 能给 agent 派任务。

### 三种 Controller 模式

| 模式 | 例子 | 实现 |
|------|------|------|
| **spawn** | 启动新 claude code 进程 | `subprocess.Popen` |
| **inject** | 给运行中的 hermes 发消息 | 写 fifo / API call |
| **schedule** | 写 cron / 触发器配置 | 修改配置文件 |

### 接口

```python
class Controller:
    mode: Literal["spawn", "inject", "schedule"]

    def dispatch_task(self, task: Task, agent: Agent) -> Run:
        """派活，返回创建的 Run"""

    def pause(self, run_id: str) -> None: ...
    def resume(self, run_id: str) -> None: ...
    def cancel(self, run_id: str) -> None: ...

    def get_status(self, run_id: str) -> RunStatus: ...
```

### "spawn" 模式细节（Claude Code 为例）

```python
def dispatch_task(self, task, agent):
    cmd = ["claude", "-p", task.description, "--output-format", "stream-json"]
    if agent.adapter_config.get("workdir"):
        cwd = agent.adapter_config["workdir"]

    proc = subprocess.Popen(cmd, cwd=cwd, stdout=PIPE, stderr=PIPE)

    # 注册到 process registry，让 Observer 也能跟上
    run = Run(session_id=..., task_id=task.id, status="running")
    self.process_registry.add(run.id, proc)
    return run
```

### "inject" 模式细节（Hermes 为例）

Hermes 有 IPC 接口，可以直接发消息给已经在跑的 session。Controller 调它的 API。

---

## Approver 协议（V0.4）

让 core 能拦截 agent 的危险操作。

### 三种 Approver 模式

| 模式 | 例子 | 实现 |
|------|------|------|
| **hook** | Claude Code 的 PreToolUse hook | 配置 hook 脚本，hook 调 core HTTP API |
| **proxy** | 把 agent 的 LLM 调用代理 | 用 mitmproxy 风格拦截 |
| **manual** | 用户在 UI 上手工触发 | core 直接发请求 |

### 接口

```python
class Approver:
    mode: Literal["hook", "proxy", "manual"]

    def setup(self) -> None:
        """安装 hook、启动 proxy 等"""

    def handle_request(self, raw_request: dict) -> ApprovalDecision:
        """收到 agent 的请求，转成 Approval 对象，等用户决策"""

    def teardown(self) -> None: ...
```

### "hook" 模式细节（Claude Code）

Claude Code 支持 `PreToolUse` hook。Adapter 安装的 hook 脚本会：

1. 收到 agent 即将执行的工具调用
2. POST 到 core 的 `/approve` 接口
3. 阻塞等响应
4. 根据响应 exit 0（允许）或 exit 2（拒绝）

```bash
# .claude/settings.json
{
  "hooks": {
    "PreToolUse": "agent-master-approve-hook"
  }
}
```

```python
# agent-master-approve-hook
def main():
    request = json.load(sys.stdin)
    response = requests.post(
        "http://127.0.0.1:8765/api/approve",
        json={"adapter": "claude_code", "request": request},
        timeout=300,  # 等 5 分钟
    )
    if response.json()["decision"] == "approve":
        sys.exit(0)
    else:
        sys.exit(2)
```

### 远程审批：checkpoint 怎么和 Approver 配合

详见 05-hitl.md。简单说：
- Approver 创建 Approval 对象，写 `checkpoint_data` 字段
- agent 进程阻塞（或被 suspend）
- 用户从手机批准 → core 把决策写回 → Approver 解除阻塞

---

## V0.1 三个 adapter 的具体规划

### OpenCode adapter

| 维度 | 实现 |
|------|------|
| Observer 数据源 | `~/.local/share/opencode/opencode.db`（SQLite + Drizzle 迁移） |
| watch 方式 | WAL tail，poll 200ms |
| session_id | `session.id`（`ses_xxx` 前缀） |
| parent_id | `session.parent_id`（直接有！） |
| event 流 | `event` 表已 event-sourced，按 `aggregate_id + seq` 读 |
| cost/tokens | session 表上有 `cost`/`tokens_input`/`tokens_output`/`tokens_reasoning`/`tokens_cache_*` |
| 已知坑 | DB 350MB，启动时只读最近 24h 的 session 防爆 |

### Claude Code adapter

| 维度 | 实现 |
|------|------|
| Observer 数据源 | `~/.claude/projects/**/*.jsonl` |
| watch 方式 | fsevents（macOS）+ inotify（linux）|
| session_id | filename stem |
| parent_id | `sidechain.parent_id` 字段 |
| 已知坑 | flush 间隔 866-988ms，是行级实时 |

### Hermes adapter

| 维度 | 实现 |
|------|------|
| Observer 数据源 | `~/.hermes/state.db`（sqlite） |
| watch 方式 | WAL tail（轮询 WAL 文件大小变化 + 增量读） |
| session_id | `sessions.id` |
| parent_id | `sessions.parent_session_id`（直接有！） |
| 已知坑 | sqlite 不通知，需要 polling，最小 200ms |

---

## Adapter 测试约定

每个 adapter 必须有：

1. **fixture 目录** `packages/adapters/<name>/fixtures/` — 真实数据样本
2. **unit test** — 用 fixture 验证 `map_event` 正确性
3. **integration test** — 用临时目录模拟 agent 输出，验证完整流程

```
packages/adapters/claude_code/
├── adapter.toml
├── observe.py
├── fixtures/
│   ├── simple_session.jsonl
│   ├── with_sidechain.jsonl
│   └── with_thinking.jsonl
└── tests/
    ├── test_map_event.py
    └── test_observe.py
```

---

## Adapter 注册机制

启动时 core 扫 `packages/adapters/*/adapter.toml`，按 `[capabilities]` 注册到对应能力的 registry。

加新 adapter = 加目录，不需要改 core 代码。

```python
# core/adapter_registry.py
def discover_adapters():
    for toml_path in Path("packages/adapters").glob("*/adapter.toml"):
        meta = tomllib.loads(toml_path.read_text())
        if meta["capabilities"].get("observer"):
            register_observer(meta["adapter"]["name"], load_module(toml_path.parent / "observe.py"))
        # 同理 controller / approver
```

---

## 与 paperclip adapter 的关系

Paperclip 的 11 个 adapter 在 `~/paperclip/packages/adapters/`，**主要是 controller 实现**
（spawn claude code、codex 等进程并管理生命周期）。

我们可以：

- **直接借鉴 paperclip 的 controller 代码**（MIT 友好）— 进程管理的脏活他们已经做了
- **不复制 paperclip 的 task model 假设** — 他们的 adapter 假设了"task 来了就 spawn"，
  我们的 observer 模式不需要

具体每个 adapter 的实现细节，会在实现阶段拆 sub-doc。
