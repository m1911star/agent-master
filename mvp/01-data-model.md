# 01 · Core Data Model (AgentRun / Turn / Step / AgentEvent / Adapter)

> This is the MVP's "constitution". Every new source integration and every new view should map back to this abstraction.

## Design principles

1. **Event stream + state snapshot, two layers** — events let you "replay" + push live; snapshot lets the UI render without recomputing. The dashboard is essentially `state = reduce(events)`
2. **Run / Turn / Step three tiers** — map to three views: **card (Run)** → **timeline (Turns)** → **detail drawer (Steps)**. Cursor / Aider / Codex / Claude are all variants of these three levels
3. **Store both `parentRunId` AND `rootRunId`** — topology grouped by `rootRunId` performs well; walking parent uses `parentRunId`
4. **Normalize `toolKind` but keep `toolName`** — the "tool kind distribution radar" on a card uses `toolKind` (10 kinds, doesn't explode); detail view shows `toolName` (user recognizes Bash/Edit)
5. **Adapter parses its own topology** — Claude's `isSidechain` only Claude adapter understands; Hermes's `parent_session_id` only Hermes adapter can read. The dashboard layer shouldn't know these dialects
6. **`MetricEvent` is its own kind** — usage data updates very frequently (every streaming chunk); separating it avoids re-rendering the step list on every metric
7. **`raw` field is mandatory** — every abstraction loses information. When debugging, having the original jsonl handy saves you
8. **Don't abstract "conversation content"** — the dashboard is a "boss" view, not an IDE. To see full content, deep-link back into Claude Code / Codex / Hermes's own UI

## TypeScript type definitions (canonical version)

```ts
// ============ 1. AgentRun ============
// Top-level container = one execution of one agent
export interface AgentRun {
  id: string;                          // globally unique, recommended: `${source}:${sessionId}`
  source: AgentSource;                 // 'claude-code' | 'codex' | 'hermes' | ...
  title: string | null;                // adapter-inferred or left empty

  // Topology
  parentRunId: string | null;          // parent run (source of subagent/spawn)
  rootRunId: string;                   // root run (for tree-level aggregation)
  spawnKind: SpawnKind;                // 'root' | 'subagent' | 'fork' | 'handoff' | 'continuation'
  spawnReason: string | null;          // e.g. the tool_call name that triggered it (Task / delegate_task / spawn)

  // Lifecycle
  status: RunStatus;
  startedAt: number;                   // epoch ms
  endedAt: number | null;
  endReason: string | null;            // 'completed' | 'user_cancelled' | 'error' | 'idle_timeout' | ...
  lastEventAt: number;                 // used to decide "live" vs "stale"

  // Execution environment
  workspace: {
    cwd: string | null;
    gitBranch: string | null;
    gitRepo: string | null;            // parsed from cwd
  };
  runtime: {
    model: string | null;              // 'claude-opus-4-5', 'gpt-5', ...
    provider: string | null;
    cliVersion: string | null;
    permissionMode: 'default' | 'plan' | 'auto' | 'yolo' | null;
  };

  // Cumulative metrics (incremental update, don't recompute)
  metrics: RunMetrics;

  // Raw source info (adapter free-form)
  raw: { filePath?: string; sourceId: string; [k: string]: unknown };
}

export type AgentSource =
  | 'claude-code' | 'codex' | 'hermes' | 'opencode' | 'omp'
  | 'cursor' | 'aider' | string;       // extensible

export type RunStatus =
  | 'live'        // active (lastEventAt within 30s)
  | 'idle'        // waiting for user input
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'stale'       // old, should be collapsed
  | 'unknown';    // process died but didn't mark complete

export type SpawnKind = 'root' | 'subagent' | 'fork' | 'handoff' | 'continuation';

export interface RunMetrics {
  turnCount: number;
  messageCount: number;
  toolCallCount: number;
  toolCallsByName: Record<string, number>;   // {Bash: 12, Read: 8, ...} for radar
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;
  estimatedCostUsd: number;
  filesEdited: number;                       // parse Edit/Write/patch tools
  shellCommandsRun: number;
  errors: number;                            // tool failure count
  durationMs: number;                        // endedAt - startedAt
}


// ============ 2. Turn ============
// One "user input → model reply" loop
export interface Turn {
  id: string;                          // `${runId}:turn:${index}`
  runId: string;
  index: number;

  status: TurnStatus;
  startedAt: number;
  endedAt: number | null;

  trigger: 'user_message' | 'tool_result' | 'auto_continue' | 'system';

  // Summary (detailed steps NOT inlined here — query events table by turnId)
  summary: {
    userPrompt: string | null;         // truncated
    assistantText: string | null;      // truncated
    toolCallsUsed: string[];           // ['Bash', 'Edit', 'Bash']
    tokens: { in: number; out: number; reasoning: number };
  };
}

export type TurnStatus =
  | 'streaming' | 'tool_pending' | 'tool_running'
  | 'completed' | 'errored' | 'cancelled';


// ============ 3. AgentEvent ============
// Unified event stream. All adapters translate any source's action into this single event kind; the dashboard consumes the event stream
export type AgentEvent =
  | RunLifecycleEvent
  | TurnEvent
  | StepEvent       // one model output (thinking / message / tool_call / tool_result)
  | MetricEvent;    // pure metric delta (usage / cost), no content

interface BaseEvent {
  eventId: string;        // monotonic within adapter (use file offset or row id)
  runId: string;
  turnId: string | null;  // lifecycle events aren't bound to a turn
  timestamp: number;      // epoch seconds (unified)
  source: AgentSource;
  monotonicSeq: number;   // adapter-internal monotonic, for cross-source time alignment
}

export interface RunLifecycleEvent extends BaseEvent {
  kind: 'run_started' | 'run_ended' | 'run_spawned_child' | 'run_handoff';
  data: {
    childRunId?: string;        // for run_spawned_child
    handoffTarget?: string;     // for run_handoff
    endReason?: string;
  };
}

export interface TurnEvent extends BaseEvent {
  kind: 'turn_started' | 'turn_completed' | 'turn_errored';
  data: { userPrompt?: string; errorMessage?: string };
}

export interface StepEvent extends BaseEvent {
  kind: 'step';
  step: Step;
}

export interface MetricEvent extends BaseEvent {
  kind: 'metric_delta';
  data: Partial<RunMetrics>;    // incremental, dashboard merges
}


// ============ 4. Step ============
// An atomic action: think / speak / call tool / get result
export type Step =
  | ThinkingStep
  | MessageStep
  | ToolCallStep
  | ToolResultStep
  | SystemStep;

interface BaseStep {
  id: string;          // unique within adapter (uuid or callId)
  startedAt: number;
  endedAt: number | null;
}

export interface ThinkingStep extends BaseStep {
  type: 'thinking';
  text: string;            // may be empty (encrypted reasoning)
  encrypted: boolean;
  tokens: number;
}

export interface MessageStep extends BaseStep {
  type: 'message';
  role: 'assistant' | 'user';
  text: string;
  streaming: boolean;
}

export interface ToolCallStep extends BaseStep {
  type: 'tool_call';
  callId: string;          // links to ToolResultStep
  toolName: string;        // 'Bash', 'Edit', 'Read', 'WebSearch', ...
  toolKind: ToolKind;      // normalized category — icon/count
  input: unknown;          // raw arguments
  inputPreview: string;    // 80-char summary for the dashboard
}

export interface ToolResultStep extends BaseStep {
  type: 'tool_result';
  callId: string;
  ok: boolean;
  output: unknown;
  outputPreview: string;
  durationMs: number;
  sideEffects?: {
    filesTouched?: string[];
    commandsRun?: string[];
    httpRequests?: number;
  };
}

export interface SystemStep extends BaseStep {
  type: 'system';
  category: 'permission_change' | 'compact' | 'rewind' | 'attachment' | 'context_warning';
  text: string;
}

export type ToolKind =
  | 'shell'      // Bash, exec_command
  | 'file_read'  // Read, cat
  | 'file_edit'  // Edit, Write, patch
  | 'search'     // Grep, Glob, search_files
  | 'web'        // WebFetch, WebSearch
  | 'mcp'        // mcp__*
  | 'spawn'      // Task, delegate_task → triggers child run
  | 'memory'     // memory/skill_view/...
  | 'thinking'
  | 'other';


// ============ 5. AgentAdapter ============
// The protocol the dashboard uses to integrate sources
export interface AgentAdapter {
  readonly source: AgentSource;
  readonly displayName: string;

  // One-shot discovery: scan FS / DB, return all known runs
  discoverRuns(): Promise<AgentRun[]>;

  // Read history: pull all events for a run (used to backfill on dashboard cold start)
  readEvents(runId: string, sinceSeq?: number): AsyncIterable<AgentEvent>;

  // Live subscription: watch for new events (FS watch / SQLite WAL tail / IPC)
  subscribe(handler: (event: AgentEvent) => void): () => void;

  // Topology resolution (optional — adapter often knows its own parent/child better)
  resolveTopology?(): Promise<TopologyEdge[]>;
}

export interface TopologyEdge {
  parentRunId: string;
  childRunId: string;
  kind: SpawnKind;
  spawnedAt: number;
  triggerStepId?: string;   // which ToolCallStep triggered it (e.g. Task tool)
}


// ============ 6. DashboardState ============
// Normalized state subscribed by the render layer, updated incrementally by the event reducer
export interface DashboardState {
  runs: Map<string, AgentRun>;
  turnsByRun: Map<string, Turn[]>;
  recentSteps: Map<string, Step[]>;     // last N steps per run
  topology: TopologyEdge[];
  liveSourceStats: Map<AgentSource, {
    adapterStatus: 'connected'|'error'|'stopped';
    lastEventAt: number;
  }>;
}
```

## Spike-stage simplifications (vs the spec above)

In the spike, several things were simplified for speed (**must be restored when upgrading**):

| Field / concept | TS spec | Spike reality |
|---|---|---|
| `Turn` intermediate tier | ✅ full | ❌ skipped, events attach directly to run |
| `rootRunId` | ✅ stored explicitly | ❌ derived by walking `parentRunId` |
| Full `RunMetrics` (11 fields) | ✅ | ⚠️ only `inputTokens / outputTokens / toolCallCount / estimatedCostUsd` populated |
| `ToolKind` normalization | ✅ 10 kinds | ❌ raw `toolName` passed through |
| `SystemStep` | ✅ 5 categories | ❌ not parsed |
| `monotonicSeq` | ✅ cross-source time alignment | ⚠️ using timestamp as fallback |
| Adapter interface | ✅ class with methods | ⚠️ Python uses `*_adapter_tick()` functions |

These are not bugs — they're MVP time-budget choices to "get multi-source working visibly first".

## Key edge cases

- **Cross-source timestamps don't align**: Codex uses ISO strings, Hermes uses float epoch, Claude uses ISO strings. Adapters **must** normalize to epoch ms, AND add `monotonicSeq` that's monotonic within the adapter (use file offset / DB id), never trust timestamp ordering
- **Live detection**: don't trust `endedAt` — many sessions are process-killed without writing an end. Use `now - lastEventAt < 30s` for live, `>1h` for stale
- **Token double-counting**: Hermes already records usage when proxying Claude/Codex — if both Hermes adapter AND Claude adapter run, **the same session gets counted twice**. Either dedup (fuzzy match by cwd+startedAt) or let the user pick a "primary source"
- **Encrypted reasoning**: Codex's `encrypted_content` is unreadable. The dashboard should honestly show "🔒 encrypted thought, N tokens" — don't pretend you can expand it
- **Subagent file pairing**: Claude's subagents live in separate jsonl files. To draw the topology edge, the adapter must walk back via `agentId` to find the `Task` tool_use that spawned them in the main jsonl. Keep this in the adapter, not the dashboard
- **Privacy**: tool input/output contains source code, secrets, private chat fragments. Default `Step.inputPreview/outputPreview` to truncated; add an explicit "expand" button. Never auto-show.
