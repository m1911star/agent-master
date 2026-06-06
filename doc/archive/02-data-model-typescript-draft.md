# 02 · Production Data Model

> Evolves MVP `AgentRun/Turn/Step/AgentEvent` into a full orchestration + observability schema.
> All new types compose with existing ones via foreign keys; nothing in `01-data-model.md` changes shape.

## Design principles (additions to MVP §01)

1. **Event-sourced core** — every mutation emits an `AgentEvent` variant; tables are projections
2. **SQLite-first** — single-file deployment; production uses WAL mode + FTS5 for search
3. **Soft-delete everywhere** — `deletedAt` column; hard-delete only via explicit vacuum
4. **Versioned schemas** — `_migrations` table tracks applied migrations; each migration is idempotent
5. **Composable policies** — approval/budget/alert rules reference each other; execution inherits from workflow

---

## TypeScript interfaces

```ts
// ═══════════════════════════════════════════════════════════════
// 1. APPROVAL — HITL gates
// ═══════════════════════════════════════════════════════════════

/** A request from an agent for human approval before proceeding */
export interface ApprovalRequest {
  id: string;                          // uuid
  runId: string;                       // FK → AgentRun.id
  turnId: string | null;               // FK → Turn.id (if triggered mid-turn)
  stepId: string | null;               // FK → Step.id (the tool_call that needs approval)

  // What triggered it
  trigger: ApprovalTrigger;
  triggerContext: {
    toolName?: string;                 // e.g. 'Bash', 'Edit'
    toolInput?: unknown;               // the actual arguments (for policy matching)
    riskLevel: 'low' | 'medium' | 'high' | 'critical';
    description: string;               // human-readable "wants to run rm -rf /"
  };

  // Lifecycle
  status: ApprovalStatus;
  createdAt: number;                   // epoch ms
  expiresAt: number | null;            // auto-deny after timeout
  decidedAt: number | null;

  // Resolution
  decision: ApprovalDecision | null;
  decidedBy: string | null;            // user id or 'policy:xxx'
  policyId: string | null;             // FK → ApprovalPolicy.id if auto-decided

  // Checkpoint (LangGraph-style: agent suspends, can resume hours later)
  checkpoint: {
    serializedState: string | null;    // opaque blob the adapter can resume from
    resumable: boolean;
  };
}

export type ApprovalTrigger =
  | 'tool_call'          // specific tool invocation
  | 'budget_exceeded'    // cost threshold hit
  | 'workflow_gate'      // explicit approval node in workflow
  | 'policy_match'       // a rule flagged the action
  | 'manual';            // user requested approval for next action

export type ApprovalStatus =
  | 'pending'
  | 'approved'
  | 'denied'
  | 'expired'
  | 'cancelled';        // run ended before decision

export interface ApprovalDecision {
  action: 'approve' | 'deny' | 'modify';
  reason: string | null;
  modifications: Record<string, unknown> | null;  // if 'modify': patched tool input
  createRule: boolean;                 // "remember this for next time"
}

/** Reusable rule: auto-approve/deny matching future requests */
export interface ApprovalPolicy {
  id: string;
  name: string;
  description: string | null;

  // Matching
  match: ApprovalPolicyMatch;

  // Action
  action: 'auto_approve' | 'auto_deny' | 'require_approval' | 'escalate';
  priority: number;                    // higher wins on conflict (0 = default)

  // Scope
  scope: {
    agentSources?: AgentSource[];      // empty = all
    workspaces?: string[];             // glob patterns on cwd
    runIds?: string[];                 // specific runs (rare)
  };

  // Metadata
  enabled: boolean;
  createdAt: number;
  createdBy: string;
  usageCount: number;                  // how many times this policy fired
  lastUsedAt: number | null;
}

export interface ApprovalPolicyMatch {
  toolNames?: string[];                // ['Bash', 'rm', 'sudo']
  toolKinds?: ToolKind[];
  inputPatterns?: string[];            // regex on JSON-stringified input
  riskLevels?: ('low' | 'medium' | 'high' | 'critical')[];
  triggerTypes?: ApprovalTrigger[];
}


// ═══════════════════════════════════════════════════════════════
// 2. WORKFLOW — DAG-based orchestration
// ═══════════════════════════════════════════════════════════════

/** A workflow template (reusable DAG definition) */
export interface Workflow {
  id: string;
  name: string;
  description: string | null;
  version: number;                     // monotonic; new version = new row

  // Graph structure
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];

  // Defaults
  defaultTimeout: number | null;       // ms, per-node default
  defaultBudgetId: string | null;      // FK → Budget.id

  // Origin
  origin: 'manual' | 'inferred';      // inferred = auto-generated from execution history
  inferredFromRunId: string | null;    // FK → AgentRun.id

  // Metadata
  createdAt: number;
  updatedAt: number;
  createdBy: string;
  tags: string[];
}

export interface WorkflowNode {
  id: string;                          // unique within workflow
  type: WorkflowNodeType;

  // Configuration
  label: string;
  config: WorkflowNodeConfig;

  // Layout (for visual editor)
  position: { x: number; y: number } | null;
}

export type WorkflowNodeType =
  | 'task'             // dispatch work to an agent
  | 'approval_gate'   // pause for HITL
  | 'condition'       // branch based on expression
  | 'parallel_fan'    // fork into N parallel branches
  | 'parallel_join'   // wait for all/any branches
  | 'trigger'         // entry point (cron, webhook, file watch)
  | 'terminal';       // end node

export interface WorkflowNodeConfig {
  // For 'task' nodes
  agentSource?: AgentSource;
  prompt?: string;                     // template with {{variables}}
  model?: string;
  timeoutMs?: number;
  budgetId?: string;                   // FK → Budget.id

  // For 'approval_gate' nodes
  approvalPolicy?: string;             // FK → ApprovalPolicy.id
  autoExpireMs?: number;

  // For 'condition' nodes
  expression?: string;                 // JS expression evaluated against node outputs
  branches?: { label: string; condition: string }[];

  // For 'trigger' nodes
  triggerType?: 'cron' | 'webhook' | 'file_watch' | 'manual' | 'event';
  triggerConfig?: Record<string, unknown>;

  // For 'parallel_fan'/'parallel_join'
  joinStrategy?: 'all' | 'any' | 'n_of_m';
  joinCount?: number;                  // for n_of_m
}

export interface WorkflowEdge {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;
  condition: string | null;            // edge taken when truthy; null = unconditional
  label: string | null;
  priority: number;                    // evaluation order for conditional edges from same source
}

/** A concrete execution of a workflow */
export interface WorkflowExecution {
  id: string;
  workflowId: string;                  // FK → Workflow.id
  workflowVersion: number;

  status: WorkflowExecutionStatus;
  startedAt: number;
  endedAt: number | null;
  triggeredBy: string;                 // user id, cron, webhook id

  // Per-node execution state
  nodeStates: Map<string, WorkflowNodeState>;

  // Linked runs (each task node spawns an AgentRun)
  runIds: string[];                    // FK → AgentRun.id[]

  // Variables flowing through the DAG
  variables: Record<string, unknown>;

  // Budget tracking for this execution
  budgetId: string | null;             // FK → Budget.id
  totalCostUsd: number;
}

export type WorkflowExecutionStatus =
  | 'running'
  | 'paused'           // waiting for approval or external input
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'timed_out';

export interface WorkflowNodeState {
  nodeId: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'waiting_approval';
  startedAt: number | null;
  endedAt: number | null;
  runId: string | null;                // FK → AgentRun.id (for task nodes)
  approvalRequestId: string | null;    // FK → ApprovalRequest.id (for gate nodes)
  output: unknown;                     // node result (passed to downstream edges)
  error: string | null;
  retryCount: number;
}


// ═══════════════════════════════════════════════════════════════
// 3. ALERTS — rule-based monitoring
// ═══════════════════════════════════════════════════════════════

export interface AlertRule {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;

  // Trigger condition
  condition: AlertCondition;

  // What to do
  actions: AlertAction[];

  // Scope
  scope: {
    agentSources?: AgentSource[];
    workspaces?: string[];             // glob patterns
    runStatuses?: RunStatus[];
  };

  // Rate limiting
  cooldownMs: number;                  // suppress duplicate fires within window
  lastFiredAt: number | null;
  fireCount: number;

  // Metadata
  createdAt: number;
  updatedAt: number;
  createdBy: string;
}

export interface AlertCondition {
  type: AlertConditionType;

  // For 'metric_threshold'
  metric?: keyof RunMetrics;
  operator?: '>' | '<' | '>=' | '<=' | '==' | '!=';
  threshold?: number;
  windowMs?: number;                   // rolling window

  // For 'pattern_match'
  eventKinds?: string[];               // filter events
  pattern?: string;                    // regex on event data

  // For 'loop_detection'
  repetitionCount?: number;            // N same tool calls in a row
  repetitionWindowMs?: number;

  // For 'stall_detection'
  stallDurationMs?: number;            // no events for this long while status=live

  // For 'composite'
  operator_logic?: 'and' | 'or';
  children?: AlertCondition[];
}

export type AlertConditionType =
  | 'metric_threshold'    // cost > $5, tokens > 100k
  | 'pattern_match'       // event contains "error", "rm -rf"
  | 'loop_detection'      // agent repeating same action
  | 'stall_detection'     // agent stopped producing events
  | 'run_status_change'   // completed/failed/cancelled
  | 'composite';          // AND/OR of other conditions

export interface AlertAction {
  type: AlertActionType;
  config: Record<string, unknown>;
}

export type AlertActionType =
  | 'notify_ui'           // toast in dashboard
  | 'notify_push'         // mobile push (via tunnel)
  | 'pause_run'           // inject approval gate
  | 'cancel_run'          // kill the agent
  | 'webhook'             // POST to URL
  | 'create_approval'     // spawn ApprovalRequest
  | 'log';                // just record in alert_history

export interface Alert {
  id: string;
  ruleId: string;                      // FK → AlertRule.id
  runId: string | null;                // FK → AgentRun.id
  workflowExecutionId: string | null;  // FK → WorkflowExecution.id

  severity: 'info' | 'warning' | 'error' | 'critical';
  title: string;
  detail: string;

  // Lifecycle
  status: 'active' | 'acknowledged' | 'resolved' | 'silenced';
  firedAt: number;
  acknowledgedAt: number | null;
  resolvedAt: number | null;
  acknowledgedBy: string | null;

  // Actions taken
  actionsTaken: { type: AlertActionType; result: 'success' | 'failed'; at: number }[];
}


// ═══════════════════════════════════════════════════════════════
// 4. BUDGET — cost tracking and limits
// ═══════════════════════════════════════════════════════════════

export interface Budget {
  id: string;
  name: string;
  description: string | null;

  // Limits
  limits: BudgetLimits;

  // Current period
  period: BudgetPeriod;
  periodStart: number;                 // epoch ms
  periodEnd: number | null;            // null = no expiry

  // Running totals (projections from CostEntry events)
  spent: BudgetSpent;

  // Behavior when exceeded
  onExceed: 'pause' | 'notify' | 'hard_stop' | 'approval_required';

  // Scope
  scope: {
    agentSources?: AgentSource[];
    workspaces?: string[];
    workflowIds?: string[];
    runIds?: string[];                 // dynamically added as runs spawn
  };

  // Metadata
  enabled: boolean;
  createdAt: number;
  updatedAt: number;
  createdBy: string;
}

export interface BudgetLimits {
  maxCostUsd: number | null;
  maxInputTokens: number | null;
  maxOutputTokens: number | null;
  maxTotalTokens: number | null;
  maxRuns: number | null;              // concurrent run cap
  maxDurationMs: number | null;        // wall clock per run
}

export type BudgetPeriod =
  | 'hourly' | 'daily' | 'weekly' | 'monthly'
  | 'per_run' | 'per_workflow' | 'lifetime';

export interface BudgetSpent {
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  runCount: number;
  oldestEntryAt: number | null;
}

/** Granular cost record — one per MetricEvent or billing-relevant action */
export interface CostEntry {
  id: string;
  budgetId: string;                    // FK → Budget.id
  runId: string;                       // FK → AgentRun.id
  turnId: string | null;               // FK → Turn.id
  workflowExecutionId: string | null;  // FK → WorkflowExecution.id

  // Cost breakdown
  costUsd: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  reasoningTokens: number;

  // Context
  model: string;
  provider: string | null;

  // Timing
  timestamp: number;
  periodKey: string;                   // '2026-06-06' or '2026-W23' — for fast GROUP BY
}


// ═══════════════════════════════════════════════════════════════
// 5. EVENT SOURCING — extended event variants
// ═══════════════════════════════════════════════════════════════

/** New event kinds extending the MVP AgentEvent union */
export type ProductionEvent = AgentEvent
  | ApprovalEvent
  | WorkflowEvent
  | AlertEvent
  | BudgetEvent;

export interface ApprovalEvent extends BaseEvent {
  kind: 'approval_requested' | 'approval_decided' | 'approval_expired';
  data: {
    approvalRequestId: string;
    decision?: ApprovalDecision;
    policyId?: string;
  };
}

export interface WorkflowEvent extends BaseEvent {
  kind: 'workflow_started' | 'workflow_node_entered' | 'workflow_node_completed'
      | 'workflow_node_failed' | 'workflow_completed' | 'workflow_failed';
  data: {
    workflowExecutionId: string;
    nodeId?: string;
    output?: unknown;
    error?: string;
  };
}

export interface AlertEvent extends BaseEvent {
  kind: 'alert_fired' | 'alert_acknowledged' | 'alert_resolved';
  data: {
    alertId: string;
    ruleId: string;
    severity: string;
    title: string;
  };
}

export interface BudgetEvent extends BaseEvent {
  kind: 'budget_threshold_warning' | 'budget_exceeded' | 'budget_reset';
  data: {
    budgetId: string;
    currentSpend: number;
    limit: number;
    percentUsed: number;
  };
}
```

---

## SQLite Schema (CREATE TABLE)

```sql
-- ═══════════════════════════════════════════════════════════════
-- MIGRATIONS TRACKING
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS _migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at INTEGER NOT NULL DEFAULT (unixepoch('now') * 1000),
  checksum   TEXT NOT NULL        -- sha256 of migration SQL
);

-- ═══════════════════════════════════════════════════════════════
-- CORE (mirrors MVP types — included for FK completeness)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS agent_runs (
  id              TEXT PRIMARY KEY,
  source          TEXT NOT NULL,
  title           TEXT,
  parent_run_id   TEXT REFERENCES agent_runs(id),
  root_run_id     TEXT NOT NULL,
  spawn_kind      TEXT NOT NULL DEFAULT 'root',
  spawn_reason    TEXT,
  status          TEXT NOT NULL DEFAULT 'unknown',
  started_at      INTEGER NOT NULL,
  ended_at        INTEGER,
  end_reason      TEXT,
  last_event_at   INTEGER NOT NULL,
  workspace_json  TEXT,           -- JSON: {cwd, gitBranch, gitRepo}
  runtime_json    TEXT,           -- JSON: {model, provider, cliVersion, permissionMode}
  metrics_json    TEXT,           -- JSON: RunMetrics
  raw_json        TEXT NOT NULL,
  deleted_at      INTEGER
);

CREATE INDEX idx_runs_root ON agent_runs(root_run_id);
CREATE INDEX idx_runs_status ON agent_runs(status, last_event_at DESC);
CREATE INDEX idx_runs_source ON agent_runs(source, started_at DESC);

CREATE TABLE IF NOT EXISTS turns (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES agent_runs(id),
  idx         INTEGER NOT NULL,
  status      TEXT NOT NULL,
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER,
  trigger     TEXT NOT NULL,
  summary_json TEXT
);

CREATE INDEX idx_turns_run ON turns(run_id, idx);

CREATE TABLE IF NOT EXISTS events (
  event_id      TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES agent_runs(id),
  turn_id       TEXT REFERENCES turns(id),
  kind          TEXT NOT NULL,
  timestamp     INTEGER NOT NULL,
  source        TEXT NOT NULL,
  monotonic_seq INTEGER NOT NULL,
  data_json     TEXT NOT NULL,       -- full event payload
  deleted_at    INTEGER
);

CREATE INDEX idx_events_run_seq ON events(run_id, monotonic_seq);
CREATE INDEX idx_events_kind ON events(kind, timestamp DESC);
CREATE INDEX idx_events_ts ON events(timestamp DESC);

-- ═══════════════════════════════════════════════════════════════
-- 1. APPROVAL
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS approval_requests (
  id                TEXT PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES agent_runs(id),
  turn_id           TEXT REFERENCES turns(id),
  step_id           TEXT,
  trigger           TEXT NOT NULL,       -- 'tool_call' | 'budget_exceeded' | ...
  trigger_context   TEXT NOT NULL,       -- JSON: {toolName, riskLevel, description, ...}
  status            TEXT NOT NULL DEFAULT 'pending',
  created_at        INTEGER NOT NULL,
  expires_at        INTEGER,
  decided_at        INTEGER,
  decision_json     TEXT,                -- JSON: ApprovalDecision
  decided_by        TEXT,
  policy_id         TEXT REFERENCES approval_policies(id),
  checkpoint_json   TEXT,                -- JSON: {serializedState, resumable}
  deleted_at        INTEGER
);

CREATE INDEX idx_approvals_status ON approval_requests(status, created_at DESC);
CREATE INDEX idx_approvals_run ON approval_requests(run_id);
CREATE INDEX idx_approvals_pending ON approval_requests(status) WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS approval_policies (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  match_json   TEXT NOT NULL,            -- JSON: ApprovalPolicyMatch
  action       TEXT NOT NULL,            -- 'auto_approve' | 'auto_deny' | ...
  priority     INTEGER NOT NULL DEFAULT 0,
  scope_json   TEXT,                     -- JSON: {agentSources, workspaces, runIds}
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   INTEGER NOT NULL,
  created_by   TEXT NOT NULL,
  usage_count  INTEGER NOT NULL DEFAULT 0,
  last_used_at INTEGER,
  deleted_at   INTEGER
);

CREATE INDEX idx_policies_action ON approval_policies(action, priority DESC)
  WHERE enabled = 1;

-- ═══════════════════════════════════════════════════════════════
-- 2. WORKFLOW
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS workflows (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  description  TEXT,
  version      INTEGER NOT NULL DEFAULT 1,
  nodes_json   TEXT NOT NULL,            -- JSON: WorkflowNode[]
  edges_json   TEXT NOT NULL,            -- JSON: WorkflowEdge[]
  default_timeout INTEGER,
  default_budget_id TEXT REFERENCES budgets(id),
  origin       TEXT NOT NULL DEFAULT 'manual',
  inferred_from_run_id TEXT REFERENCES agent_runs(id),
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL,
  created_by   TEXT NOT NULL,
  tags_json    TEXT,                      -- JSON: string[]
  deleted_at   INTEGER,
  UNIQUE(id, version)
);

CREATE INDEX idx_workflows_name ON workflows(name);
CREATE INDEX idx_workflows_origin ON workflows(origin);

CREATE TABLE IF NOT EXISTS workflow_executions (
  id                TEXT PRIMARY KEY,
  workflow_id       TEXT NOT NULL REFERENCES workflows(id),
  workflow_version  INTEGER NOT NULL,
  status            TEXT NOT NULL DEFAULT 'running',
  started_at        INTEGER NOT NULL,
  ended_at          INTEGER,
  triggered_by      TEXT NOT NULL,
  node_states_json  TEXT NOT NULL,        -- JSON: Record<nodeId, WorkflowNodeState>
  run_ids_json      TEXT,                 -- JSON: string[]
  variables_json    TEXT,                 -- JSON: Record<string, unknown>
  budget_id         TEXT REFERENCES budgets(id),
  total_cost_usd    REAL NOT NULL DEFAULT 0,
  deleted_at        INTEGER
);

CREATE INDEX idx_wf_exec_workflow ON workflow_executions(workflow_id, started_at DESC);
CREATE INDEX idx_wf_exec_status ON workflow_executions(status);

-- ═══════════════════════════════════════════════════════════════
-- 3. ALERTS
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS alert_rules (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  description    TEXT,
  enabled        INTEGER NOT NULL DEFAULT 1,
  condition_json TEXT NOT NULL,           -- JSON: AlertCondition
  actions_json   TEXT NOT NULL,           -- JSON: AlertAction[]
  scope_json     TEXT,                    -- JSON: {agentSources, workspaces, runStatuses}
  cooldown_ms    INTEGER NOT NULL DEFAULT 60000,
  last_fired_at  INTEGER,
  fire_count     INTEGER NOT NULL DEFAULT 0,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  created_by     TEXT NOT NULL,
  deleted_at     INTEGER
);

CREATE INDEX idx_alert_rules_enabled ON alert_rules(enabled) WHERE enabled = 1;

CREATE TABLE IF NOT EXISTS alerts (
  id                      TEXT PRIMARY KEY,
  rule_id                 TEXT NOT NULL REFERENCES alert_rules(id),
  run_id                  TEXT REFERENCES agent_runs(id),
  workflow_execution_id   TEXT REFERENCES workflow_executions(id),
  severity                TEXT NOT NULL,
  title                   TEXT NOT NULL,
  detail                  TEXT,
  status                  TEXT NOT NULL DEFAULT 'active',
  fired_at                INTEGER NOT NULL,
  acknowledged_at         INTEGER,
  resolved_at             INTEGER,
  acknowledged_by         TEXT,
  actions_taken_json      TEXT,            -- JSON: {type, result, at}[]
  deleted_at              INTEGER
);

CREATE INDEX idx_alerts_status ON alerts(status, fired_at DESC);
CREATE INDEX idx_alerts_run ON alerts(run_id);
CREATE INDEX idx_alerts_severity ON alerts(severity, status);

-- ═══════════════════════════════════════════════════════════════
-- 4. BUDGET
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS budgets (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  description    TEXT,
  limits_json    TEXT NOT NULL,           -- JSON: BudgetLimits
  period         TEXT NOT NULL,           -- 'daily' | 'weekly' | ...
  period_start   INTEGER NOT NULL,
  period_end     INTEGER,
  spent_json     TEXT NOT NULL DEFAULT '{}', -- JSON: BudgetSpent (materialized)
  on_exceed      TEXT NOT NULL DEFAULT 'notify',
  scope_json     TEXT,                    -- JSON: {agentSources, workspaces, ...}
  enabled        INTEGER NOT NULL DEFAULT 1,
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL,
  created_by     TEXT NOT NULL,
  deleted_at     INTEGER
);

CREATE INDEX idx_budgets_period ON budgets(period, period_start);

CREATE TABLE IF NOT EXISTS cost_entries (
  id                      TEXT PRIMARY KEY,
  budget_id               TEXT NOT NULL REFERENCES budgets(id),
  run_id                  TEXT NOT NULL REFERENCES agent_runs(id),
  turn_id                 TEXT REFERENCES turns(id),
  workflow_execution_id   TEXT REFERENCES workflow_executions(id),
  cost_usd                REAL NOT NULL,
  input_tokens            INTEGER NOT NULL DEFAULT 0,
  output_tokens           INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens       INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens      INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens        INTEGER NOT NULL DEFAULT 0,
  model                   TEXT NOT NULL,
  provider                TEXT,
  timestamp               INTEGER NOT NULL,
  period_key              TEXT NOT NULL    -- '2026-06-06' for fast GROUP BY
);

CREATE INDEX idx_cost_budget_period ON cost_entries(budget_id, period_key);
CREATE INDEX idx_cost_run ON cost_entries(run_id, timestamp);
CREATE INDEX idx_cost_ts ON cost_entries(timestamp DESC);
CREATE INDEX idx_cost_model ON cost_entries(model, period_key);

-- ═══════════════════════════════════════════════════════════════
-- 5. SEARCH INDEXES (FTS5)
-- ═══════════════════════════════════════════════════════════════

-- Full-text search over events (tool inputs, outputs, messages)
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
  event_id UNINDEXED,
  run_id UNINDEXED,
  kind UNINDEXED,
  content,                              -- searchable text extracted from data_json
  tokenize='porter unicode61'
);

-- Full-text search over agent run titles and workspace info
CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(
  run_id UNINDEXED,
  title,
  workspace_cwd,
  git_branch,
  git_repo,
  tokenize='porter unicode61'
);

-- Full-text search over workflow names/descriptions
CREATE VIRTUAL TABLE IF NOT EXISTS workflows_fts USING fts5(
  workflow_id UNINDEXED,
  name,
  description,
  tags,
  tokenize='porter unicode61'
);

-- Full-text search over alerts
CREATE VIRTUAL TABLE IF NOT EXISTS alerts_fts USING fts5(
  alert_id UNINDEXED,
  title,
  detail,
  tokenize='porter unicode61'
);

-- ═══════════════════════════════════════════════════════════════
-- 6. MATERIALIZED VIEWS (triggers keep these in sync)
-- ═══════════════════════════════════════════════════════════════

-- Daily cost rollup for fast dashboard queries
CREATE TABLE IF NOT EXISTS cost_daily_rollup (
  date_key    TEXT NOT NULL,             -- '2026-06-06'
  budget_id   TEXT NOT NULL REFERENCES budgets(id),
  model       TEXT NOT NULL,
  source      TEXT NOT NULL,
  total_cost  REAL NOT NULL DEFAULT 0,
  total_input_tokens  INTEGER NOT NULL DEFAULT 0,
  total_output_tokens INTEGER NOT NULL DEFAULT 0,
  run_count   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (date_key, budget_id, model, source)
);

-- Active approval summary (for badge counts)
CREATE TABLE IF NOT EXISTS approval_summary (
  pending_count   INTEGER NOT NULL DEFAULT 0,
  oldest_pending  INTEGER,               -- epoch ms of oldest pending request
  updated_at      INTEGER NOT NULL
);
```

---

## Relation diagram (text)

```
┌──────────────┐      ┌──────────┐      ┌─────────────┐
│  AgentRun    │──1:N─│  Turn    │──1:N─│   Event     │
│  (existing)  │      └──────────┘      └─────────────┘
│              │                               │
│              │──1:N─┌──────────────────┐     │ kind=approval_*
│              │      │ ApprovalRequest  │◄────┘
│              │      └────────┬─────────┘
│              │               │ decided_by policy
│              │      ┌────────▼─────────┐
│              │      │ ApprovalPolicy   │
│              │      └──────────────────┘
│              │
│              │──1:N─┌──────────────────┐
│              │      │   CostEntry      │──N:1─┌─────────┐
│              │      └──────────────────┘      │ Budget  │
│              │                                └─────────┘
│              │──N:1─┌──────────────────┐
│              │      │WorkflowExecution │──N:1─┌──────────┐
│              │      └──────────────────┘      │ Workflow │
└──────────────┘                                └──────────┘
       │
       │──1:N─┌──────────────────┐
              │     Alert        │──N:1─┌────────────┐
              └──────────────────┘      │ AlertRule  │
                                        └────────────┘
```

---

## Event sourcing patterns

### Append-only event log

Every state mutation is first recorded as an event, then projected into tables:

```ts
// Core event processing pipeline
async function processEvent(event: ProductionEvent): Promise<void> {
  // 1. Append to events table (source of truth)
  await db.run(
    `INSERT INTO events (event_id, run_id, turn_id, kind, timestamp, source, monotonic_seq, data_json)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [event.eventId, event.runId, event.turnId, event.kind,
     event.timestamp, event.source, event.monotonicSeq, JSON.stringify(event)]
  );

  // 2. Update FTS index
  const searchableText = extractSearchableText(event);
  if (searchableText) {
    await db.run(
      `INSERT INTO events_fts (event_id, run_id, kind, content) VALUES (?, ?, ?, ?)`,
      [event.eventId, event.runId, event.kind, searchableText]
    );
  }

  // 3. Project into domain tables (idempotent upserts)
  switch (event.kind) {
    case 'approval_requested':
      await projectApprovalRequest(event);
      break;
    case 'approval_decided':
      await projectApprovalDecision(event);
      break;
    case 'budget_exceeded':
      await projectBudgetExceeded(event);
      break;
    case 'metric_delta':
      await projectCostEntry(event);
      await updateBudgetSpent(event);
      break;
    case 'workflow_node_completed':
      await projectNodeState(event);
      break;
    // ... other projections
  }
}
```

### Replay / rebuild projections

```ts
// Rebuild any projection from event log (disaster recovery, schema change)
async function rebuildProjection(
  table: string,
  projector: (event: ProductionEvent) => Promise<void>
): Promise<void> {
  await db.run(`DELETE FROM ${table}`);
  const events = db.iterate<ProductionEvent>(
    `SELECT data_json FROM events ORDER BY monotonic_seq`
  );
  for await (const row of events) {
    const event = JSON.parse(row.data_json);
    await projector(event);
  }
}
```

### Snapshot + event hybrid

For hot-path reads (dashboard polling), use materialized state; for audit/replay, use events:

```ts
// Dashboard read path: snapshot tables (fast, denormalized)
// Audit/compliance path: events table (complete, append-only)
// Reconciliation: periodic rebuild validates snapshot == reduce(events)
```

---

## Migration patterns

### Migration runner

```ts
interface Migration {
  version: number;
  name: string;
  up: string;     // SQL to apply
  down: string;   // SQL to rollback (best-effort)
}

async function migrate(db: Database, migrations: Migration[]): Promise<void> {
  const applied = await db.all<{ version: number }>(
    `SELECT version FROM _migrations ORDER BY version`
  );
  const appliedSet = new Set(applied.map(r => r.version));

  for (const m of migrations.sort((a, b) => a.version - b.version)) {
    if (appliedSet.has(m.version)) continue;

    await db.run('BEGIN');
    try {
      await db.exec(m.up);
      await db.run(
        `INSERT INTO _migrations (version, name, checksum) VALUES (?, ?, ?)`,
        [m.version, m.name, sha256(m.up)]
      );
      await db.run('COMMIT');
    } catch (err) {
      await db.run('ROLLBACK');
      throw new Error(`Migration ${m.version} (${m.name}) failed: ${err}`);
    }
  }
}
```

### Version sequence

```ts
const MIGRATIONS: Migration[] = [
  {
    version: 1,
    name: 'core_tables',
    up: `-- agent_runs, turns, events (from MVP)`,
    down: `DROP TABLE events; DROP TABLE turns; DROP TABLE agent_runs;`
  },
  {
    version: 2,
    name: 'approval_system',
    up: `-- approval_requests, approval_policies`,
    down: `DROP TABLE approval_requests; DROP TABLE approval_policies;`
  },
  {
    version: 3,
    name: 'workflow_system',
    up: `-- workflows, workflow_executions`,
    down: `DROP TABLE workflow_executions; DROP TABLE workflows;`
  },
  {
    version: 4,
    name: 'alert_system',
    up: `-- alert_rules, alerts`,
    down: `DROP TABLE alerts; DROP TABLE alert_rules;`
  },
  {
    version: 5,
    name: 'budget_system',
    up: `-- budgets, cost_entries, cost_daily_rollup`,
    down: `DROP TABLE cost_daily_rollup; DROP TABLE cost_entries; DROP TABLE budgets;`
  },
  {
    version: 6,
    name: 'fts_indexes',
    up: `-- events_fts, runs_fts, workflows_fts, alerts_fts`,
    down: `DROP TABLE alerts_fts; DROP TABLE workflows_fts; DROP TABLE runs_fts; DROP TABLE events_fts;`
  },
  {
    version: 7,
    name: 'materialized_views',
    up: `-- cost_daily_rollup, approval_summary`,
    down: `DROP TABLE approval_summary; DROP TABLE cost_daily_rollup;`
  },
];
```

### Zero-downtime migration strategy

1. **Additive-only schema changes** — new columns default NULL; new tables have no effect until code uses them
2. **Dual-write window** — old code writes to old columns; new code writes to both; after deploy, backfill
3. **FTS rebuild on version bump** — FTS5 `rebuild` command after schema change:
   ```sql
   INSERT INTO events_fts(events_fts) VALUES('rebuild');
   ```
4. **WAL mode mandatory** — readers never block writers:
   ```sql
   PRAGMA journal_mode = WAL;
   PRAGMA busy_timeout = 5000;
   PRAGMA synchronous = NORMAL;
   ```

---

## Query patterns (common dashboard operations)

```sql
-- Pending approvals with run context
SELECT ar.*, r.title, r.source, r.status as run_status
FROM approval_requests ar
JOIN agent_runs r ON r.id = ar.run_id
WHERE ar.status = 'pending'
ORDER BY ar.created_at ASC;

-- Cost by model/day for the last 7 days
SELECT period_key, model, SUM(cost_usd) as total
FROM cost_entries
WHERE timestamp > (unixepoch('now') - 604800) * 1000
GROUP BY period_key, model
ORDER BY period_key DESC;

-- Active workflows with completion percentage
SELECT we.id, w.name, we.status,
  (SELECT COUNT(*) FROM json_each(we.node_states_json)
   WHERE json_extract(value, '$.status') = 'completed') as completed_nodes,
  json_array_length(w.nodes_json) as total_nodes
FROM workflow_executions we
JOIN workflows w ON w.id = we.workflow_id
WHERE we.status = 'running';

-- Full-text search across events
SELECT e.run_id, e.kind, e.timestamp, snippet(events_fts, 3, '<b>', '</b>', '...', 20) as match
FROM events_fts
JOIN events e ON e.event_id = events_fts.event_id
WHERE events_fts MATCH ?
ORDER BY e.timestamp DESC
LIMIT 50;

-- Loop detection: same tool called >5 times consecutively
WITH consecutive AS (
  SELECT run_id, kind,
    json_extract(data_json, '$.step.toolName') as tool,
    ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY monotonic_seq) -
    ROW_NUMBER() OVER (PARTITION BY run_id, json_extract(data_json, '$.step.toolName')
                       ORDER BY monotonic_seq) as grp
  FROM events
  WHERE kind = 'step' AND json_extract(data_json, '$.step.type') = 'tool_call'
    AND timestamp > (unixepoch('now') - 3600) * 1000
)
SELECT run_id, tool, COUNT(*) as streak
FROM consecutive
GROUP BY run_id, tool, grp
HAVING COUNT(*) > 5;
```
