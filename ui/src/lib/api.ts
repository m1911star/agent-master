/**
 * Thin REST client over /api/v1. Uses TanStack Query for caching;
 * raw `apiFetch` for one-shot calls.
 */

import type {
  Agent,
  AgentEvent,
  InternalMetrics,
  Run,
  Session,
} from "./types"

const BASE = "/api/v1"

export class ApiError extends Error {
  status: number
  constructor(status: number, msg: string) {
    super(msg)
    this.status = status
  }
}

async function apiFetch<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) {
    let body = ""
    try { body = await r.text() } catch { /* ignore */ }
    throw new ApiError(r.status, `${r.status} ${path}: ${body || r.statusText}`)
  }
  return r.json() as Promise<T>
}

export const api = {
  listAgents: () => apiFetch<Agent[]>("/agents"),
  listSessions: (opts?: { activeOnly?: boolean; limit?: number }) => {
    const q = new URLSearchParams()
    if (opts?.activeOnly) q.set("active_only", "true")
    if (opts?.limit) q.set("limit", String(opts.limit))
    const qs = q.toString()
    return apiFetch<Session[]>(`/sessions${qs ? "?" + qs : ""}`)
  },
  getSession: (id: string) => apiFetch<Session>(`/sessions/${id}`),
  listSessionEvents: (id: string, opts?: { afterSeq?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (opts?.afterSeq != null) q.set("after_seq", String(opts.afterSeq))
    if (opts?.limit) q.set("limit", String(opts.limit))
    const qs = q.toString()
    return apiFetch<AgentEvent[]>(`/sessions/${id}/events${qs ? "?" + qs : ""}`)
  },
  getRun: (id: string) => apiFetch<Run>(`/runs/${id}`),
  listRunEvents: (id: string, opts?: { kind?: string; afterSeq?: number; limit?: number }) => {
    const q = new URLSearchParams()
    if (opts?.kind) q.set("kind", opts.kind)
    if (opts?.afterSeq != null) q.set("after_seq", String(opts.afterSeq))
    if (opts?.limit) q.set("limit", String(opts.limit))
    const qs = q.toString()
    return apiFetch<AgentEvent[]>(`/runs/${id}/events${qs ? "?" + qs : ""}`)
  },
  metrics: () => apiFetch<InternalMetrics>("/internal/metrics"),
  health: () => apiFetch<{ status: string; version: string; uptime_seconds: number }>("/health"),
}

export function streamUrl(channel: "global" | { runId: string }): string {
  if (channel === "global") return `${BASE}/stream/global`
  return `${BASE}/stream/run/${channel.runId}`
}
