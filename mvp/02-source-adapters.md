# 02 · Real Data Shapes & Adapter Implementation for 5 Sources

> This is the "archeology log" — where each source stores its data, in what format, and what the key fields mean.
> When integrating a new source, follow this template to investigate first.

## Overview

| Source | Storage | Real-time-ness | Topology signal | Tested? |
|---|---|---|---|---|
| **claude-code** | `~/.claude/projects/*/*.jsonl` (one file per session) | median 866ms/line | `isSidechain=true` + `agentId` + `subagents/*.jsonl` | ✅ 30 runs |
| **codex** | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | median 14ms/line (streaming) | none (linear) | ✅ 3–30 runs |
| **hermes** | `~/.hermes/state.db` (SQLite + FTS5) | written immediately | `parent_session_id` (continuation) | ✅ 67–95 runs |
| **opencode** | `~/.local/share/opencode/opencode.db` (SQLite + Drizzle ORM) | written immediately | `session.parent_id` (subagent) | ✅ 100 runs, 134 topology edges |
| **omp** (oh-my-pi / pi-coding-agent) | `~/.omp/agent/sessions/<workspace>/<ts>_<uuid>.jsonl` | line-level | none (linear) | ✅ 6 runs |

> ⚠️ The user mentioned "oh-my-pi and pure pi"; after investigation these are **the same tool** (npm package `@oh-my-pi/pi-coding-agent`, binary called `omp`, directory `~/.omp`). MVP uses the single source name `omp`.

## Claude Code

**Main file paths**:
```
~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl
~/.claude/projects/<encoded-cwd>/subagents/agent-<agentId>.jsonl
```

`encoded-cwd` rewrites `/Users/horus` as `-Users-horus`.

**Event type distribution** (measured across 30 files):

```
{'assistant': 878, 'user': 585, 'attachment': 126, 'system': 64,
 'file-history-snapshot': 48, 'permission-mode': 33, 'last-prompt': 31, ...}
```

**Actual assistant message structure**:

```json
{
  "parentUuid": "...",
  "isSidechain": false,         // ← key: subagent marker
  "type": "assistant",
  "uuid": "...",
  "timestamp": "2026-05-30T14:25:36.486Z",
  "cwd": "/Users/horus/...",
  "sessionId": "8a8183a9-...",
  "version": "2.1.132",
  "gitBranch": "HEAD",
  "agentId": "...",             // ← only present in subagent files
  "message": {
    "id": "...",
    "role": "assistant",
    "content": [
      {"type": "tool_use", "id": "...", "name": "Bash", "input": {...}}
    ],
    "model": "...",
    "stop_reason": "...",
    "usage": {
      "input_tokens": 34000,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0,
      "output_tokens": 144,
      "service_tier": "standard"
    }
  }
}
```

**Tool results live in user messages** (type=user with content containing a `tool_result` block).

**Adapter translation rules** (spike implementation, `claude_adapter_tick` in `witness.py`):

| Source signal | Target |
|---|---|
| Filename / sessionId | `runId = claude-code:{sessionId}` |
| `agentId` (subagents subdir) | Separate run, `parentRunId = claude-code:{sessionId}`, `spawnKind = subagent` |
| `content.tool_use` block | `ToolCallStep` |
| `content.tool_result` block (user msg) | `ToolResultStep` |
| `content.text` block | `MessageStep` |
| `message.usage` | `MetricEvent` |
| `cwd / version / gitBranch` | `AgentRun.workspace + runtime` |

**Pitfalls**:
- Streaming flush rate ~866ms — 60× slower than codex. The dashboard's "live feel" comes mainly from codex/opencode
- Subagent file prime reads only last 8KB, missing the first-line `cwd`, resulting in cwd=?. See backlog in §05

## Codex (OpenAI CLI)

**Path**: `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<sessionId>.jsonl`

**Event types** (by occurrence):

```
session_meta (1)         → header, defines session
turn_context (N)         → environment info at the start of each turn
event_msg (N)            → lifecycle signals (task_started, task_complete, etc.)
response_item (most)     → model output (reasoning / message / function_call / function_call_output)
```

**Key payload shapes**:

```json
// session_meta.payload
{
  "id": "019de34a-...",
  "timestamp": "...",
  "cwd": "/Users/...",
  "originator": "codex_exec",  // or codex-tui
  "cli_version": "0.118.0",
  "model_provider": "..."
}

// turn_context.payload
{
  "turn_id": "...",
  "cwd": "...",
  "model": "...",
  "approval_policy": "never",
  "sandbox_policy": {...}
}

// response_item.payload (function_call)
{
  "type": "function_call",
  "name": "exec_command",
  "arguments": "{\"cmd\":\"...\"}",   // ← JSON string
  "call_id": "call_..."
}

// response_item.payload (function_call_output)
{
  "type": "function_call_output",
  "call_id": "call_...",            // ← pairs with the function_call above
  "output": "Command: /bin/zsh ..."
}

// response_item.payload (reasoning)
{
  "type": "reasoning",
  "summary": [...],                  // may be empty
  "content": null,                   // usually null
  "encrypted_content": "..."         // ← unreadable, show as "🔒 encrypted"
}
```

**Adapter translation rules**:

| Source signal | Target |
|---|---|
| `session_meta.payload.id` | `runId = codex:{id}` |
| `turn_context` | update `runtime.model` |
| `function_call` | `ToolCallStep` |
| `function_call_output` (paired by call_id) | `ToolResultStep` |
| `reasoning` | `ThinkingStep` (`encrypted: true` if encrypted_content) |
| `message` (text / output_text) | `MessageStep` |
| `event_msg.task_started` | `TurnEvent.turn_started` |

**Pitfalls**:
- Codex writes very fast (median 14ms between lines). This is the main source of the dashboard's "live feel"
- Lots of reasoning is encrypted — don't pretend you can expand it
- A single session runs everything in one run — **no subagents**

## Hermes

**Path**: `~/.hermes/state.db` (single SQLite)

**Key schema tables**:

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,              -- 'cli' | 'acp' | ...
  parent_session_id TEXT,            -- ← key topology signal (continuation)
  cwd TEXT,
  model TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  title TEXT,
  message_count INTEGER,
  tool_call_count INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  estimated_cost_usd REAL,
  handoff_state TEXT,                -- ← cross-platform handoff signal
  handoff_platform TEXT,
  ...
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,  -- ← monotonic, used for stream tail
  session_id TEXT REFERENCES sessions(id),
  role TEXT NOT NULL,                    -- 'user' | 'assistant' | 'tool' | 'system'
  content TEXT,
  tool_calls TEXT,                       -- JSON array
  tool_name TEXT,
  timestamp REAL NOT NULL,
  token_count INTEGER,
  reasoning TEXT,
  reasoning_details TEXT,
  ...
);

-- Also FTS5 tables messages_fts / messages_fts_trigram for full-text search
```

**Adapter translation rules**:

| Source signal | Target |
|---|---|
| `sessions.id` | `runId = hermes:{id}` |
| `sessions.parent_session_id` | `parentRunId`, `spawnKind = continuation` |
| `sessions.title` | `title` |
| `sessions.{cwd, model, ...}` | `workspace + runtime` |
| `sessions.{*_tokens, estimated_cost_usd}` | `metrics` |
| `messages.tool_calls` (JSON parse) | `ToolCallStep` |
| `messages.role='tool'` | `ToolResultStep` |
| `messages.role in ('user','assistant')` + content | `MessageStep` |
| `messages.handoff_*` | (not yet) `run_handoff` event |

**SQLite tail strategy**:
- `WHERE id > last_seen_id ORDER BY id ASC LIMIT 200`
- Read-only mode: `sqlite3.connect(f"file:{HERMES_DB}?mode=ro", uri=True)`
- No WAL watching needed; 1s polling is enough (Hermes writes are visible immediately)

**Pitfalls**:
- Hermes itself proxies Claude/Codex (as "proxied conversations") — **double-counting** issue unsolved
- Old sessions have no `cwd` (field added later) → run card shows `cwd=?`

## OpenCode

**Path**: `~/.local/share/opencode/opencode.db` (SQLite, Drizzle ORM)

**Three-tier schema**:

```sql
CREATE TABLE session (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  parent_id TEXT,                    -- ← key: subagent topology (OpenCode subagents are very active)
  slug TEXT NOT NULL,
  directory TEXT NOT NULL,
  title TEXT NOT NULL,
  agent TEXT,                        -- e.g. 'Sisyphus - ultraworker'
  model TEXT,                        -- JSON string: {"id":"...","providerID":"..."}
  cost REAL DEFAULT 0,
  tokens_input INTEGER,
  tokens_output INTEGER,
  time_created INTEGER,              -- epoch ms
  time_updated INTEGER,
  ...
);

CREATE TABLE message (
  id TEXT PRIMARY KEY,
  session_id TEXT,
  time_created INTEGER,              -- epoch ms
  data TEXT                          -- contains role/mode/agent/...
);

CREATE TABLE part (
  id TEXT PRIMARY KEY,
  message_id TEXT,
  session_id TEXT,
  time_created INTEGER,
  data TEXT                          -- ← the actual event payload
);
```

**`part.data` type distribution** (measured on last 200 records):

```
text          33     → MessageStep (assistant text block)
reasoning     20     → ThinkingStep
tool          41     → ToolCallStep / ToolResultStep (by state.status)
step-start    47     → not used yet
step-finish   47     → MetricEvent (contains tokens / cost)
patch         12     → ToolCallStep with toolName='patch', input = files list
```

**Structure of `tool` part**:

```json
{
  "type": "tool",
  "tool": "bash",        // or read/edit/grep/...
  "callID": "...",
  "state": {
    "status": "running" | "completed" | "error",
    "input": {...},
    "output": "..."     // only when completed
  }
}
```

**Adapter translation rules**:

| Source signal | Target |
|---|---|
| `session.id` | `runId = opencode:{id}` |
| `session.parent_id` | `parentRunId`, `spawnKind = subagent` |
| `session.directory` | `workspace.cwd` |
| `session.model` (JSON parse) | `runtime.model + provider` |
| `session.agent` | `runtime.agentName` |
| `part.type='text'` | `MessageStep` |
| `part.type='reasoning'` | `ThinkingStep` |
| `part.type='tool'` + status=running | `ToolCallStep` |
| `part.type='tool'` + status=completed/error | `ToolResultStep` |
| `part.type='patch'` | `ToolCallStep` (toolName='patch') |
| `part.type='step-finish'` | `MetricEvent` |

**Pitfalls**:
- OpenCode is **the topology-richest source** in the spike (134 edges) — it spawns subagents very actively
- A single session may produce hundreds of parts; stream tail uses `time_created > last_ms` with `LIMIT 300` to stay snappy
- Large `tool_output` lives in `~/.local/share/opencode/tool-output/` separately; spike doesn't parse those (uses `state.output` directly)

## omp (oh-my-pi / pi-coding-agent)

**Identity clarification**:
- npm package: `@oh-my-pi/pi-coding-agent`
- binary name: `omp` (not `pi`)
- data directory: `~/.omp/`
- The user's "oh-my-pi vs pure pi" — both are the same tool

**Paths**:

```
~/.omp/agent/sessions/<workspace-encoded>/<timestamp>_<uuid>.jsonl
~/.omp/agent/sessions/<workspace-encoded>/<timestamp>_<uuid>/   (snapshot directory)
~/.omp/agent/agent.db                 (SQLite, mostly auth / model state)
~/.omp/agent/history.db               (SQLite, cross-session history)
```

`workspace-encoded` rewrites `/private/tmp` as `--private-tmp--`.

**jsonl event types**:

```
session                  → header
model_change            → runtime.model update
thinking_level_change   → currently ignored
message                 → main content (contains user / assistant / tool roles)
```

**`message.message` structure** (when assistant):

```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "..."},
    {"type": "tool_use", "id": "...", "name": "...", "input": {...}},
    {"type": "thinking", "thinking": "..."}
  ]
}
```

User / tool roles are analogous (thinking blocks are an omp signature).

**Adapter translation rules**:

| Source signal | Target |
|---|---|
| `session.id` | `runId = omp:{id}` |
| `session.title / cwd` | `workspace + title` |
| `model_change.model` | `runtime.model` |
| `message.role='user'`, content text | `MessageStep` (role=user) |
| `message.role='assistant'` content `text` block | `MessageStep` (role=assistant) |
| `message.role='assistant'` content `tool_use` block | `ToolCallStep` |
| `message.role='assistant'` content `thinking` block | `ThinkingStep` |
| `message.role='tool'` | `ToolResultStep` |

**Pitfalls**:
- omp is the youngest of the 5 sources (pi-coding-agent v15.x); the event format may shift in future versions
- Snapshot directory (same-name subdirectory) holds file snapshots; not yet parsed by the dashboard

## Checklist for adding a new source (template)

When you integrate a new source, investigate in this order:

1. **Find the data**: scan `~/.config/`, `~/.local/share/`, `~/Library/Application Support/`, `~/.<name>/`
2. **Identify the format**: jsonl stream? SQLite? multiple SQLite? plain log?
3. **Sample**: read 3–5 rows / lines + look at the schema; record the keys for each event type
4. **Find the topology signal**: any parent_id? isSidechain? handoff? Or completely none (linear)?
5. **Find the timestamp format**: ISO string / epoch seconds / epoch ms? Normalize to epoch seconds
6. **Find the monotonic cursor**: SQLite id? file byte offset? timestamp? (used for stream tail)
7. **Write `adapter_tick()` function**, referencing the existing 5 adapters
8. **Test**: run the spike and watch the stats counters tick up
9. **Add UI**: 5 places need source color + chip (CSS / chips / SHORT / COLORS / activeSrc)
