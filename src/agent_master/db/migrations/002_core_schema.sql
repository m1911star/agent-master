-- 002_core_schema.sql
-- 11 tables: 7 core + 4 satellite objects per doc/01-data-model.md.
-- Strictly mirrors the per-object field tables and the §关系约束 block.
--
-- Conventions (doc/01 §命名约定):
--   * snake_case plural table names
--   * primary key column always named `id`
--   * timestamps as TEXT in ISO-8601 (sqlite has no native datetime — apps parse)
--   * enums stored as TEXT, validated in application layer
--   * foreign keys explicit; CASCADE/SET NULL per §关系约束
--
-- Note: SQLite does not enforce FK by default; the connection layer issues
-- `PRAGMA foreign_keys = ON` (see db/connection.py).

-- ─────────────────────────────────────────────────────────────────────────
-- 1. agents
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    adapter_type    TEXT NOT NULL,
    adapter_config  TEXT,                 -- JSON
    status          TEXT NOT NULL DEFAULT 'idle',
    capabilities    TEXT,                 -- JSON array
    budget_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
CREATE INDEX IF NOT EXISTS idx_agents_adapter_type ON agents(adapter_type);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. sessions
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    external_id         TEXT,
    parent_session_id   TEXT,
    workdir             TEXT,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    last_active_at      TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
    summary             TEXT,
    meta                TEXT,             -- JSON
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id) ON DELETE SET NULL
);
-- Per doc/01 §关键索引
CREATE INDEX IF NOT EXISTS idx_sessions_agent_status_active
    ON sessions(agent_id, status, last_active_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_external_id ON sessions(external_id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 3. runs
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    task_id         TEXT,
    trigger         TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    exit_reason     TEXT,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    cost_usd        TEXT NOT NULL DEFAULT '0',  -- Decimal serialized as text
    summary         TEXT,
    error_message   TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

-- ─────────────────────────────────────────────────────────────────────────
-- 4. events  (high-frequency table — autoincrement bigserial-equivalent)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    ts          TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    stream      TEXT,
    level       TEXT,
    color       TEXT,
    text        TEXT,
    payload     TEXT,                  -- JSON
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
-- Per doc/01 §关键索引
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_run_kind ON events(run_id, kind);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

-- ─────────────────────────────────────────────────────────────────────────
-- 5. tasks  (V0.2+ but schema lands now per doc/01)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    parent_task_id      TEXT,
    assignee_agent_id   TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_by          TEXT NOT NULL,
    priority            INTEGER NOT NULL DEFAULT 50,
    goal_chain          TEXT,             -- JSON array
    created_at          TEXT NOT NULL,
    started_at          TEXT,
    completed_at        TEXT,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id) ON DELETE SET NULL,
    FOREIGN KEY (assignee_agent_id) REFERENCES agents(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 6. approvals  (V0.4 but schema lands now per doc/05 §V0.1 准备工作)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id                  TEXT PRIMARY KEY,
    run_id              TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    requested_at        TEXT NOT NULL,
    decided_at          TEXT,
    expires_at          TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    default_action      TEXT NOT NULL DEFAULT 'reject',
    subject             TEXT NOT NULL,
    detail              TEXT,             -- JSON
    decision_by         TEXT,
    decision_reason     TEXT,
    rule_created_id     TEXT,
    checkpoint_data     TEXT,             -- JSON, LangGraph-style
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE,
    FOREIGN KEY (rule_created_id) REFERENCES rules(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_agent ON approvals(agent_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 7. artifacts
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS artifacts (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    kind            TEXT NOT NULL,
    title           TEXT NOT NULL,
    path            TEXT,
    content_hash    TEXT,
    size_bytes      INTEGER,
    created_at      TEXT NOT NULL,
    meta            TEXT,                 -- JSON
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind);

-- ─────────────────────────────────────────────────────────────────────────
-- 8. budgets  (satellite, V0.2 enforcement)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budgets (
    id              TEXT PRIMARY KEY,
    scope           TEXT NOT NULL,        -- agent | task | global
    period          TEXT NOT NULL,        -- day | month | total
    limit_tokens    INTEGER,
    limit_usd       TEXT,                 -- Decimal as text
    spent_tokens    INTEGER NOT NULL DEFAULT 0,
    spent_usd       TEXT NOT NULL DEFAULT '0',
    on_exceed       TEXT NOT NULL DEFAULT 'pause'
);
CREATE INDEX IF NOT EXISTS idx_budgets_scope ON budgets(scope);

-- ─────────────────────────────────────────────────────────────────────────
-- 9. rules  (satellite, V0.4 HITL pattern allowlist)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rules (
    id                          TEXT PRIMARY KEY,
    agent_id                    TEXT,
    pattern                     TEXT NOT NULL,
    action                      TEXT NOT NULL,
    scope                       TEXT NOT NULL DEFAULT 'permanent',
    expires_at                  TEXT,
    created_from_approval_id    TEXT,
    created_at                  TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rules_agent ON rules(agent_id);
CREATE INDEX IF NOT EXISTS idx_rules_action ON rules(action);

-- ─────────────────────────────────────────────────────────────────────────
-- 10. workflows  (satellite, V0.3)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS workflows (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    definition_yaml     TEXT NOT NULL,
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_name ON workflows(name);

-- ─────────────────────────────────────────────────────────────────────────
-- 11. audit_log  (satellite, all mutations)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    actor           TEXT NOT NULL,
    action          TEXT NOT NULL,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    payload         TEXT,                 -- JSON
    ip_addr         TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor);
CREATE INDEX IF NOT EXISTS idx_audit_log_target ON audit_log(target_type, target_id);
