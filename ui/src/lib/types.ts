/**
 * Mirrors the Python dataclasses in src/agent_master/models/*.
 * Keep these in sync — the API serializes to_dict() output.
 */

export type AgentStatus =
  | "idle" | "busy" | "paused" | "error" | "offline"

export interface Agent {
  id: string
  name: string
  adapter_type: string
  adapter_config: Record<string, unknown>
  status: AgentStatus
  capabilities: string[]
  budget_id: string | null
  created_at: string
  updated_at: string
}

export type SessionStatus = "active" | "idle" | "closed"

export interface Session {
  id: string
  agent_id: string
  external_id: string | null
  parent_session_id: string | null
  workdir: string
  started_at: string
  ended_at: string | null
  last_active_at: string
  status: SessionStatus
  summary: string | null
  meta: Record<string, unknown>
}

export type RunStatus =
  | "pending" | "running" | "success" | "failed" | "interrupted"

export interface Run {
  id: string
  session_id: string
  task_id: string | null
  trigger: string
  started_at: string
  ended_at: string | null
  status: RunStatus
  exit_reason: string | null
  tokens_in: number
  tokens_out: number
  cost_usd: string
  summary: string | null
  error_message: string | null
}

export type EventKind =
  | "user_message"
  | "assistant_message"
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "status_change"
  | "approval_requested"
  | "approval_decided"
  | "artifact_created"
  | "error"
  | "session_start" | "session_end"
  | "run_start" | "run_end"
  | "raw"

export interface AgentEvent {
  id: number | null
  run_id: string
  seq: number
  ts: string | null
  created_at: string | null
  kind: EventKind
  stream: string | null
  level: string | null
  color: string | null
  text: string | null
  payload: Record<string, unknown> | null
}

export interface InternalMetrics {
  uptime_seconds: number
  pipeline: {
    events_received: number
    events_persisted: number
    broadcasts_sent: number
    broadcasts_dropped: number
  }
  broker: {
    channels: string[]
    subscriber_total: number
  }
}
