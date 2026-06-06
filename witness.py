#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 m1911star
"""
Agent Witness — multi-agent observability spike.

Watches Claude Code, Codex, and Hermes session stores; normalizes their
events into a unified AgentEvent schema; serves a live web dashboard at
http://127.0.0.1:8765 that shows:

  - Run cards (one per agent run, color-coded by source)
  - Live event stream (last N events across all runs)
  - Parent/child topology (which run spawned which subagent)

Zero dependencies. Pure stdlib. Pure file polling (no fsevents). Designed
to prove the multi-source unification works, not to be production-fast.
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable

# ============================================================
# Config
# ============================================================
HOME = os.path.expanduser("~")
CLAUDE_GLOB = f"{HOME}/.claude/projects/**/*.jsonl"
CODEX_GLOB = f"{HOME}/.codex/sessions/**/*.jsonl"
HERMES_DB = f"{HOME}/.hermes/state.db"
OPENCODE_DB = f"{HOME}/.local/share/opencode/opencode.db"
OMP_GLOB = f"{HOME}/.omp/agent/sessions/**/*.jsonl"

POLL_INTERVAL_SEC = 1.0          # Poll cadence
LIVE_THRESHOLD_SEC = 30          # lastEventAt within → status='live'
IDLE_THRESHOLD_SEC = 600         # within → 'idle', beyond → 'stale'
MAX_EVENTS_RETAINED = 500        # Cap server-side ring buffer
PORT = 8765

# ============================================================
# Normalized data model (the AgentEvent schema)
# ============================================================
class State:
    """In-memory ring buffer + run registry."""
    def __init__(self):
        self.runs: dict[str, dict] = {}                 # runId -> AgentRun
        self.events: deque[dict] = deque(maxlen=MAX_EVENTS_RETAINED)
        self.topology: list[dict] = []                  # parent/child edges
        self.lock = threading.Lock()
        # Per-file byte offset so we only read new bytes each poll
        self.file_offsets: dict[str, int] = {}
        # Hermes: last seen message id
        self.hermes_last_msg_id: int = 0
        self.hermes_known_sessions: set[str] = set()
        # OpenCode: last seen part time_created (epoch ms)
        self.opencode_last_part_ms: int = 0
        self.opencode_last_session_ms: int = 0

    def upsert_run(self, run: dict) -> None:
        with self.lock:
            existing = self.runs.get(run["id"])
            if existing:
                existing.update({k: v for k, v in run.items() if v is not None})
            else:
                self.runs[run["id"]] = run

    def add_event(self, event: dict) -> None:
        with self.lock:
            self.events.append(event)
            # Update run's lastEventAt
            run = self.runs.get(event["runId"])
            if run:
                run["lastEventAt"] = event["timestamp"]
                # increment quick metrics
                m = run.setdefault("metrics", {})
                m["eventCount"] = m.get("eventCount", 0) + 1
                if event["kind"] == "step" and event["step"]["type"] == "tool_call":
                    tname = event["step"]["toolName"]
                    m.setdefault("toolCallsByName", {})
                    m["toolCallsByName"][tname] = m["toolCallsByName"].get(tname, 0) + 1
                    m["toolCallCount"] = m.get("toolCallCount", 0) + 1

    def add_topology_edge(self, parent: str, child: str, kind: str) -> None:
        with self.lock:
            edge = {"parentRunId": parent, "childRunId": child, "kind": kind}
            if edge not in self.topology:
                self.topology.append(edge)

    def snapshot(self, max_runs: int = 200) -> dict:
        with self.lock:
            now = time.time()
            runs_out = []
            for r in self.runs.values():
                r2 = dict(r)
                last = r2.get("lastEventAt") or r2.get("startedAt", 0)
                age = now - last
                if r2.get("endedAt"):
                    r2["status"] = "completed"
                elif age < LIVE_THRESHOLD_SEC:
                    r2["status"] = "live"
                elif age < IDLE_THRESHOLD_SEC:
                    r2["status"] = "idle"
                else:
                    r2["status"] = "stale"
                r2["ageSec"] = round(age, 1)
                runs_out.append(r2)
            runs_out.sort(key=lambda r: r.get("lastEventAt") or r.get("startedAt", 0), reverse=True)
            total_runs = len(runs_out)
            # Truncate to keep payload small. UI only renders top 100 anyway.
            runs_out = runs_out[:max_runs]
            visible_run_ids = {r["id"] for r in runs_out}
            # Keep topology edges only between visible runs (massive reduction)
            topo_out = [
                e for e in self.topology
                if e["parentRunId"] in visible_run_ids or e["childRunId"] in visible_run_ids
            ]
            return {
                "runs": runs_out,
                "events": list(self.events)[-100:][::-1],
                "topology": topo_out,
                "stats": {
                    "totalRuns": total_runs,
                    "visibleRuns": len(runs_out),
                    "liveRuns": sum(1 for r in runs_out if r["status"] == "live"),
                    "totalEventsRetained": len(self.events),
                    "totalTopologyEdges": len(self.topology),
                    "lastPollAt": now,
                },
            }


STATE = State()


def now_ms() -> int:
    return int(time.time() * 1000)


def parse_iso(s: str | None) -> float | None:
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


# ============================================================
# Adapter base: each adapter polls its source and pushes events
# ============================================================

# ----- Claude Code adapter -----
def claude_adapter_tick():
    """
    Each .jsonl file = one run.
    sessionId is in every line; cwd/version/gitBranch in early lines.
    Subagent files live under .../subagents/agent-*.jsonl and have an
    `agentId` field — we treat each agent file as its own run, parented
    to the main run (same sessionId).
    """
    files = glob.glob(CLAUDE_GLOB, recursive=True)
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        offset = STATE.file_offsets.get(f, 0)
        if offset >= size:
            continue
        try:
            with open(f, "r", errors="ignore") as fh:
                fh.seek(offset)
                buf = fh.read()
                STATE.file_offsets[f] = fh.tell()
        except OSError:
            continue
        if not buf.strip():
            continue

        is_subagent = "/subagents/" in f
        for line in buf.splitlines():
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            session_id = obj.get("sessionId")
            agent_id = obj.get("agentId")
            if not session_id:
                continue

            if is_subagent and agent_id:
                run_id = f"claude-code:{session_id}:{agent_id}"
                parent_run_id = f"claude-code:{session_id}"
                spawn_kind = "subagent"
            else:
                run_id = f"claude-code:{session_id}"
                parent_run_id = None
                spawn_kind = "root"

            ts = parse_iso(obj.get("timestamp")) or time.time()
            # Register/update run from metadata-bearing lines
            if "cwd" in obj or obj.get("type") in ("attachment", "user", "assistant"):
                STATE.upsert_run({
                    "id": run_id,
                    "source": "claude-code",
                    "title": obj.get("agentId") or session_id[:8],
                    "parentRunId": parent_run_id,
                    "spawnKind": spawn_kind,
                    "startedAt": ts if run_id not in STATE.runs else None,
                    "lastEventAt": ts,
                    "workspace": {
                        "cwd": obj.get("cwd"),
                        "gitBranch": obj.get("gitBranch"),
                    },
                    "runtime": {
                        "cliVersion": obj.get("version"),
                    },
                    "raw": {"filePath": f},
                })
                if parent_run_id:
                    STATE.add_topology_edge(parent_run_id, run_id, "subagent")

            etype = obj.get("type")
            if etype == "assistant":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict): continue
                        bt = block.get("type")
                        if bt == "tool_use":
                            STATE.add_event({
                                "eventId": f"{run_id}:{obj.get('uuid','')}",
                                "runId": run_id,
                                "timestamp": ts,
                                "source": "claude-code",
                                "kind": "step",
                                "step": {
                                    "type": "tool_call",
                                    "toolName": block.get("name", "?"),
                                    "callId": block.get("id"),
                                    "inputPreview": json.dumps(block.get("input", {}), ensure_ascii=False)[:120],
                                },
                            })
                        elif bt == "text":
                            txt = (block.get("text") or "").strip()
                            if txt:
                                STATE.add_event({
                                    "eventId": f"{run_id}:{obj.get('uuid','')}:msg",
                                    "runId": run_id,
                                    "timestamp": ts,
                                    "source": "claude-code",
                                    "kind": "step",
                                    "step": {"type": "message", "text": txt[:200]},
                                })
                # token usage
                usage = msg.get("usage", {})
                if usage:
                    STATE.add_event({
                        "eventId": f"{run_id}:{obj.get('uuid','')}:usage",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "claude-code",
                        "kind": "metric_delta",
                        "data": {"in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)},
                    })
            elif etype == "user":
                msg = obj.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            out = block.get("content", "")
                            out_text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
                            STATE.add_event({
                                "eventId": f"{run_id}:{obj.get('uuid','')}:tr",
                                "runId": run_id,
                                "timestamp": ts,
                                "source": "claude-code",
                                "kind": "step",
                                "step": {
                                    "type": "tool_result",
                                    "callId": block.get("tool_use_id"),
                                    "outputPreview": out_text[:120],
                                    "ok": not block.get("is_error", False),
                                },
                            })


# ----- Codex adapter -----
def codex_adapter_tick():
    files = glob.glob(CODEX_GLOB, recursive=True)
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        offset = STATE.file_offsets.get(f, 0)
        if offset >= size:
            continue
        try:
            with open(f, "r", errors="ignore") as fh:
                fh.seek(offset)
                buf = fh.read()
                STATE.file_offsets[f] = fh.tell()
        except OSError:
            continue
        if not buf.strip():
            continue

        # Filename has UUID, use as runId stem (gets overwritten by session_meta.id)
        fallback_id = Path(f).stem.split("-")[-1]
        run_id = f"codex:{fallback_id}"

        for line in buf.splitlines():
            line = line.strip()
            if not line: continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = parse_iso(obj.get("timestamp")) or time.time()
            t = obj.get("type")
            payload = obj.get("payload", {})

            if t == "session_meta":
                run_id = f"codex:{payload.get('id', fallback_id)}"
                STATE.upsert_run({
                    "id": run_id,
                    "source": "codex",
                    "title": payload.get("originator") or payload.get("id", fallback_id)[:8],
                    "parentRunId": None,
                    "spawnKind": "root",
                    "startedAt": ts,
                    "lastEventAt": ts,
                    "workspace": {"cwd": payload.get("cwd")},
                    "runtime": {
                        "cliVersion": payload.get("cli_version"),
                        "provider": payload.get("model_provider"),
                    },
                    "raw": {"filePath": f},
                })
            elif t == "turn_context":
                # Update model/cwd info on the run
                STATE.upsert_run({
                    "id": run_id,
                    "workspace": {"cwd": payload.get("cwd")},
                    "runtime": {"model": payload.get("model")},
                })
            elif t == "response_item":
                rt = payload.get("type")
                if rt == "function_call":
                    args_str = payload.get("arguments", "")
                    STATE.add_event({
                        "eventId": f"{run_id}:{payload.get('call_id', '')}",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "codex",
                        "kind": "step",
                        "step": {
                            "type": "tool_call",
                            "toolName": payload.get("name", "?"),
                            "callId": payload.get("call_id"),
                            "inputPreview": (args_str if isinstance(args_str, str) else json.dumps(args_str))[:120],
                        },
                    })
                elif rt == "function_call_output":
                    STATE.add_event({
                        "eventId": f"{run_id}:{payload.get('call_id','')}:out",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "codex",
                        "kind": "step",
                        "step": {
                            "type": "tool_result",
                            "callId": payload.get("call_id"),
                            "outputPreview": str(payload.get("output", ""))[:120],
                            "ok": True,
                        },
                    })
                elif rt == "reasoning":
                    enc = bool(payload.get("encrypted_content"))
                    text = ""
                    if isinstance(payload.get("summary"), list):
                        text = " ".join(s.get("text", "") for s in payload["summary"] if isinstance(s, dict))[:200]
                    STATE.add_event({
                        "eventId": f"{run_id}:thinking:{ts}",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "codex",
                        "kind": "step",
                        "step": {"type": "thinking", "text": text or "(encrypted)", "encrypted": enc},
                    })
                elif rt == "message":
                    content = payload.get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                                txt = (c.get("text") or "").strip()
                                if txt:
                                    STATE.add_event({
                                        "eventId": f"{run_id}:msg:{ts}",
                                        "runId": run_id,
                                        "timestamp": ts,
                                        "source": "codex",
                                        "kind": "step",
                                        "step": {"type": "message", "text": txt[:200]},
                                    })
            elif t == "event_msg":
                etype = payload.get("type")
                if etype == "task_started":
                    STATE.add_event({
                        "eventId": f"{run_id}:turn_start:{ts}",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "codex",
                        "kind": "turn_started",
                        "data": {"turnId": payload.get("turn_id")},
                    })


# ----- Hermes adapter (SQLite tail) -----
def hermes_adapter_tick():
    if not os.path.exists(HERMES_DB):
        return
    try:
        con = sqlite3.connect(f"file:{HERMES_DB}?mode=ro", uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return
    try:
        # New/updated sessions
        for row in con.execute("""
            SELECT id, source, title, parent_session_id, cwd, model,
                   started_at, ended_at, message_count, tool_call_count,
                   input_tokens, output_tokens, estimated_cost_usd
            FROM sessions
            ORDER BY started_at DESC LIMIT 100
        """):
            run_id = f"hermes:{row['id']}"
            parent = f"hermes:{row['parent_session_id']}" if row['parent_session_id'] else None
            STATE.upsert_run({
                "id": run_id,
                "source": "hermes",
                "title": row["title"] or row["id"][:14],
                "parentRunId": parent,
                "spawnKind": "continuation" if parent else "root",
                "startedAt": row["started_at"],
                "endedAt": row["ended_at"],
                "lastEventAt": row["started_at"],   # refined below
                "workspace": {"cwd": row["cwd"]},
                "runtime": {"model": row["model"]},
                "metrics": {
                    "messageCount": row["message_count"] or 0,
                    "toolCallCount": row["tool_call_count"] or 0,
                    "inputTokens": row["input_tokens"] or 0,
                    "outputTokens": row["output_tokens"] or 0,
                    "estimatedCostUsd": row["estimated_cost_usd"] or 0,
                },
            })
            if parent:
                STATE.add_topology_edge(parent, run_id, "continuation")

        # New messages
        for row in con.execute("""
            SELECT id, session_id, role, content, tool_name, tool_calls, timestamp
            FROM messages WHERE id > ? ORDER BY id ASC LIMIT 200
        """, (STATE.hermes_last_msg_id,)):
            run_id = f"hermes:{row['session_id']}"
            ts = row["timestamp"]
            if row["tool_calls"]:
                try:
                    tcs = json.loads(row["tool_calls"])
                    if isinstance(tcs, list):
                        for tc in tcs:
                            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                            STATE.add_event({
                                "eventId": f"{run_id}:{row['id']}:{tc.get('id','')}",
                                "runId": run_id,
                                "timestamp": ts,
                                "source": "hermes",
                                "kind": "step",
                                "step": {
                                    "type": "tool_call",
                                    "toolName": fn.get("name", "?"),
                                    "callId": tc.get("id"),
                                    "inputPreview": str(fn.get("arguments", ""))[:120],
                                },
                            })
                except (json.JSONDecodeError, TypeError):
                    pass
            elif row["role"] == "tool":
                STATE.add_event({
                    "eventId": f"{run_id}:{row['id']}",
                    "runId": run_id,
                    "timestamp": ts,
                    "source": "hermes",
                    "kind": "step",
                    "step": {
                        "type": "tool_result",
                        "outputPreview": (row["content"] or "")[:120],
                        "ok": True,
                    },
                })
            elif row["role"] in ("assistant", "user") and row["content"]:
                STATE.add_event({
                    "eventId": f"{run_id}:{row['id']}",
                    "runId": run_id,
                    "timestamp": ts,
                    "source": "hermes",
                    "kind": "step",
                    "step": {
                        "type": "message",
                        "role": row["role"],
                        "text": (row["content"] or "")[:200],
                    },
                })
            STATE.hermes_last_msg_id = max(STATE.hermes_last_msg_id, row["id"])
    finally:
        con.close()


# ----- OpenCode adapter (SQLite tail of opencode.db) -----
def opencode_adapter_tick():
    """
    OpenCode stores everything in ~/.local/share/opencode/opencode.db.

    Schema (simplified):
      session(id, project_id, parent_id, slug, directory, title,
              agent, model, time_created, time_updated,
              cost, tokens_input, ...)
      message(id, session_id, time_created, time_updated, data JSON)
      part(id, message_id, session_id, time_created, data JSON)

    `part.data` is the actual event payload — type ∈ {text, reasoning,
    tool, step-start, step-finish, patch}. We map:
      text/reasoning → message/thinking step
      tool → tool_call (state contains call/result)
      patch → file_edit side-effect
      step-finish → metric_delta (tokens/cost)
    """
    if not os.path.exists(OPENCODE_DB):
        return
    try:
        con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return
    try:
        # Sessions: load up to 100 most recent (cheap, no streaming needed)
        for row in con.execute("""
            SELECT id, project_id, parent_id, directory, title,
                   agent, model, time_created, time_updated,
                   cost, tokens_input, tokens_output
            FROM session
            ORDER BY time_updated DESC LIMIT 100
        """):
            run_id = f"opencode:{row['id']}"
            parent = f"opencode:{row['parent_id']}" if row['parent_id'] else None
            # model is stored as JSON string '{"id":"...","providerID":"..."}'
            model_str = row["model"] or ""
            try:
                m = json.loads(model_str) if model_str.startswith("{") else {}
                model_name = m.get("id", model_str)
                provider = m.get("providerID")
            except (json.JSONDecodeError, TypeError):
                model_name = model_str
                provider = None
            STATE.upsert_run({
                "id": run_id,
                "source": "opencode",
                "title": row["title"] or row["id"][:14],
                "parentRunId": parent,
                "spawnKind": "subagent" if parent else "root",
                "startedAt": row["time_created"] / 1000.0 if row["time_created"] else None,
                "lastEventAt": row["time_updated"] / 1000.0 if row["time_updated"] else None,
                "workspace": {"cwd": row["directory"]},
                "runtime": {"model": model_name, "provider": provider, "agentName": row["agent"]},
                "metrics": {
                    "estimatedCostUsd": row["cost"] or 0,
                    "inputTokens": row["tokens_input"] or 0,
                    "outputTokens": row["tokens_output"] or 0,
                },
            })
            if parent:
                STATE.add_topology_edge(parent, run_id, "subagent")

        # Parts: stream new ones since last seen time_created
        # Limit 300 per tick to avoid event flood
        for row in con.execute("""
            SELECT id, message_id, session_id, time_created, data
            FROM part
            WHERE time_created > ?
            ORDER BY time_created ASC LIMIT 300
        """, (STATE.opencode_last_part_ms,)):
            run_id = f"opencode:{row['session_id']}"
            ts = row["time_created"] / 1000.0
            try:
                p = json.loads(row["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(p, dict):
                continue
            ptype = p.get("type")
            if ptype == "text":
                txt = (p.get("text") or "").strip()
                if txt:
                    STATE.add_event({
                        "eventId": f"{run_id}:{row['id']}",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "opencode",
                        "kind": "step",
                        "step": {"type": "message", "role": "assistant", "text": txt[:200]},
                    })
            elif ptype == "reasoning":
                txt = (p.get("text") or "").strip()
                STATE.add_event({
                    "eventId": f"{run_id}:{row['id']}",
                    "runId": run_id,
                    "timestamp": ts,
                    "source": "opencode",
                    "kind": "step",
                    "step": {"type": "thinking", "text": txt[:200], "encrypted": False},
                })
            elif ptype == "tool":
                # OpenCode tool parts have nested state{status, input, output}
                tool_name = p.get("tool", "?")
                state = p.get("state") or {}
                status = state.get("status")
                if status in ("running", "pending", None):
                    # tool_call event
                    inp = state.get("input") or {}
                    STATE.add_event({
                        "eventId": f"{run_id}:{row['id']}:call",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "opencode",
                        "kind": "step",
                        "step": {
                            "type": "tool_call",
                            "toolName": tool_name,
                            "callId": p.get("callID"),
                            "inputPreview": (json.dumps(inp, ensure_ascii=False) if inp else "")[:120],
                        },
                    })
                elif status in ("completed", "error"):
                    out = state.get("output") or state.get("error") or ""
                    STATE.add_event({
                        "eventId": f"{run_id}:{row['id']}:result",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "opencode",
                        "kind": "step",
                        "step": {
                            "type": "tool_result",
                            "callId": p.get("callID"),
                            "ok": status == "completed",
                            "outputPreview": str(out)[:120],
                        },
                    })
            elif ptype == "patch":
                files = p.get("files") or []
                STATE.add_event({
                    "eventId": f"{run_id}:{row['id']}:patch",
                    "runId": run_id,
                    "timestamp": ts,
                    "source": "opencode",
                    "kind": "step",
                    "step": {
                        "type": "tool_call",
                        "toolName": "patch",
                        "callId": p.get("hash"),
                        "inputPreview": f"{len(files)} files: " + ", ".join(str(f)[:30] for f in files[:3]),
                    },
                })
            # step-start / step-finish are turn boundaries — useful for metric_delta
            elif ptype == "step-finish":
                tokens = p.get("tokens") or {}
                cost = p.get("cost")
                if tokens or cost:
                    STATE.add_event({
                        "eventId": f"{run_id}:{row['id']}:metric",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "opencode",
                        "kind": "metric_delta",
                        "data": {
                            "in": tokens.get("input", 0) if isinstance(tokens, dict) else 0,
                            "out": tokens.get("output", 0) if isinstance(tokens, dict) else 0,
                        },
                    })
            STATE.opencode_last_part_ms = max(STATE.opencode_last_part_ms, row["time_created"])
    finally:
        con.close()


# ----- omp / oh-my-pi adapter (jsonl session files) -----
def omp_adapter_tick():
    """
    omp (oh-my-pi / pi-coding-agent) stores sessions at:
      ~/.omp/agent/sessions/<workspace>/<timestamp>_<uuid>.jsonl

    Each line is one of:
      {type: 'session', id, timestamp, cwd, title}
      {type: 'model_change', timestamp, model}
      {type: 'thinking_level_change', timestamp, thinkingLevel}
      {type: 'message', timestamp, message: {role, content, ...}}
    """
    files = glob.glob(OMP_GLOB, recursive=True)
    for f in files:
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        offset = STATE.file_offsets.get(f, 0)
        if offset >= size:
            continue
        try:
            with open(f, "r", errors="ignore") as fh:
                fh.seek(offset)
                buf = fh.read()
                STATE.file_offsets[f] = fh.tell()
        except OSError:
            continue
        if not buf.strip():
            continue

        # workspace name = parent directory
        workspace = Path(f).parent.name.replace("--", "/")
        run_id_fallback = f"omp:{Path(f).stem}"
        run_id = run_id_fallback

        for line in buf.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = obj.get("type")
            ts = parse_iso(obj.get("timestamp")) or time.time()

            if t == "session":
                sid = obj.get("id")
                if sid:
                    run_id = f"omp:{sid}"
                STATE.upsert_run({
                    "id": run_id,
                    "source": "omp",
                    "title": obj.get("title") or workspace,
                    "parentRunId": None,
                    "spawnKind": "root",
                    "startedAt": ts,
                    "lastEventAt": ts,
                    "workspace": {"cwd": obj.get("cwd") or workspace},
                    "raw": {"filePath": f},
                })
            elif t == "model_change":
                STATE.upsert_run({
                    "id": run_id,
                    "runtime": {"model": obj.get("model")},
                })
            elif t == "message":
                msg = obj.get("message") or {}
                role = msg.get("role")
                content = msg.get("content")

                if role == "user":
                    # content is usually a list of {type:'text', text:'...'} or a string
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        text = " ".join(
                            c.get("text", "") for c in content
                            if isinstance(c, dict) and c.get("type") == "text"
                        )
                    if text.strip():
                        STATE.add_event({
                            "eventId": f"{run_id}:{obj.get('id', '')}",
                            "runId": run_id,
                            "timestamp": ts,
                            "source": "omp",
                            "kind": "step",
                            "step": {"type": "message", "role": "user", "text": text[:200]},
                        })
                elif role == "assistant":
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            bt = block.get("type")
                            if bt == "text":
                                txt = (block.get("text") or "").strip()
                                if txt:
                                    STATE.add_event({
                                        "eventId": f"{run_id}:{obj.get('id','')}:txt",
                                        "runId": run_id,
                                        "timestamp": ts,
                                        "source": "omp",
                                        "kind": "step",
                                        "step": {"type": "message", "role": "assistant", "text": txt[:200]},
                                    })
                            elif bt == "tool_use":
                                STATE.add_event({
                                    "eventId": f"{run_id}:{obj.get('id','')}:{block.get('id','')}",
                                    "runId": run_id,
                                    "timestamp": ts,
                                    "source": "omp",
                                    "kind": "step",
                                    "step": {
                                        "type": "tool_call",
                                        "toolName": block.get("name", "?"),
                                        "callId": block.get("id"),
                                        "inputPreview": json.dumps(block.get("input", {}), ensure_ascii=False)[:120],
                                    },
                                })
                            elif bt == "thinking":
                                txt = (block.get("thinking") or block.get("text") or "").strip()
                                STATE.add_event({
                                    "eventId": f"{run_id}:{obj.get('id','')}:think",
                                    "runId": run_id,
                                    "timestamp": ts,
                                    "source": "omp",
                                    "kind": "step",
                                    "step": {"type": "thinking", "text": txt[:200], "encrypted": False},
                                })
                elif role == "tool":
                    # tool_result message
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict):
                                text += c.get("text", "") or c.get("content", "")
                    STATE.add_event({
                        "eventId": f"{run_id}:{obj.get('id','')}:tr",
                        "runId": run_id,
                        "timestamp": ts,
                        "source": "omp",
                        "kind": "step",
                        "step": {
                            "type": "tool_result",
                            "callId": msg.get("tool_use_id"),
                            "ok": not msg.get("is_error", False),
                            "outputPreview": text[:120],
                        },
                    })


# ============================================================
# Poller thread
# ============================================================
def poller_loop():
    # Strategy: don't fully skip history, but seed each source with a
    # recent backlog so the dashboard has visible events on first load.
    print("[witness] priming: seeding adapters with recent activity...")
    # Hermes: replay last 50 messages into the event stream
    if os.path.exists(HERMES_DB):
        try:
            con = sqlite3.connect(f"file:{HERMES_DB}?mode=ro", uri=True, timeout=2.0)
            r = con.execute("SELECT MAX(id) FROM messages").fetchone()
            if r and r[0]:
                STATE.hermes_last_msg_id = max(0, r[0] - 50)  # replay last 50
            con.close()
        except sqlite3.OperationalError:
            pass

    # OpenCode: replay parts from the last 60s, so the dashboard has fresh content
    if os.path.exists(OPENCODE_DB):
        try:
            con = sqlite3.connect(f"file:{OPENCODE_DB}?mode=ro", uri=True, timeout=2.0)
            r = con.execute("SELECT MAX(time_created) FROM part").fetchone()
            if r and r[0]:
                STATE.opencode_last_part_ms = max(0, r[0] - 60_000)  # last 60 seconds
            con.close()
        except sqlite3.OperationalError:
            pass

    # Claude/Codex/omp: seed file offsets to (size - 8KB) so the last few events
    # of recently-active files get replayed.
    BACKFILL_BYTES = 8192
    for f in glob.glob(CLAUDE_GLOB, recursive=True):
        try:
            sz = os.path.getsize(f)
            STATE.file_offsets[f] = max(0, sz - BACKFILL_BYTES)
        except OSError:
            pass
    for f in glob.glob(CODEX_GLOB, recursive=True):
        try:
            sz = os.path.getsize(f)
            STATE.file_offsets[f] = max(0, sz - BACKFILL_BYTES)
        except OSError:
            pass
    for f in glob.glob(OMP_GLOB, recursive=True):
        try:
            sz = os.path.getsize(f)
            STATE.file_offsets[f] = max(0, sz - BACKFILL_BYTES)
        except OSError:
            pass

    # But DO load run cards so the dashboard isn't empty on first load.
    print("[witness] loading run cards (no events)...")
    try: hermes_adapter_tick()
    except Exception as e: print(f"[hermes] error: {e}")
    # For Claude/Codex, just register the most recent files as run cards
    for f in sorted(glob.glob(CLAUDE_GLOB, recursive=True), key=os.path.getmtime, reverse=True)[:30]:
        try:
            with open(f, "r", errors="ignore") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                        if obj.get("sessionId") and obj.get("cwd"):
                            is_sub = "/subagents/" in f
                            sid = obj["sessionId"]
                            aid = obj.get("agentId")
                            if is_sub and aid:
                                run_id = f"claude-code:{sid}:{aid}"
                                STATE.upsert_run({
                                    "id": run_id, "source": "claude-code",
                                    "title": aid, "parentRunId": f"claude-code:{sid}",
                                    "spawnKind": "subagent",
                                    "startedAt": parse_iso(obj.get("timestamp")) or os.path.getmtime(f),
                                    "lastEventAt": os.path.getmtime(f),
                                    "workspace": {"cwd": obj.get("cwd"), "gitBranch": obj.get("gitBranch")},
                                    "raw": {"filePath": f},
                                })
                                STATE.add_topology_edge(f"claude-code:{sid}", run_id, "subagent")
                            else:
                                STATE.upsert_run({
                                    "id": f"claude-code:{sid}", "source": "claude-code",
                                    "title": sid[:8], "parentRunId": None, "spawnKind": "root",
                                    "startedAt": parse_iso(obj.get("timestamp")) or os.path.getmtime(f),
                                    "lastEventAt": os.path.getmtime(f),
                                    "workspace": {"cwd": obj.get("cwd"), "gitBranch": obj.get("gitBranch")},
                                    "raw": {"filePath": f},
                                })
                            break
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    for f in sorted(glob.glob(CODEX_GLOB, recursive=True), key=os.path.getmtime, reverse=True)[:30]:
        try:
            with open(f, "r", errors="ignore") as fh:
                first = fh.readline()
                obj = json.loads(first)
                if obj.get("type") == "session_meta":
                    p = obj.get("payload", {})
                    rid = f"codex:{p.get('id','?')}"
                    STATE.upsert_run({
                        "id": rid, "source": "codex",
                        "title": p.get("originator") or p.get("id","?")[:8],
                        "parentRunId": None, "spawnKind": "root",
                        "startedAt": parse_iso(p.get("timestamp")) or os.path.getmtime(f),
                        "lastEventAt": os.path.getmtime(f),
                        "workspace": {"cwd": p.get("cwd")},
                        "runtime": {"cliVersion": p.get("cli_version"), "provider": p.get("model_provider")},
                        "raw": {"filePath": f},
                    })
        except (OSError, json.JSONDecodeError):
            continue

    print(f"[witness] primed: {len(STATE.runs)} historical runs registered as cards")
    print(f"[witness] watching for new activity (poll every {POLL_INTERVAL_SEC}s)")

    while True:
        try: claude_adapter_tick()
        except Exception as e: print(f"[claude] tick error: {e}")
        try: codex_adapter_tick()
        except Exception as e: print(f"[codex] tick error: {e}")
        try: hermes_adapter_tick()
        except Exception as e: print(f"[hermes] tick error: {e}")
        try: opencode_adapter_tick()
        except Exception as e: print(f"[opencode] tick error: {e}")
        try: omp_adapter_tick()
        except Exception as e: print(f"[omp] tick error: {e}")
        time.sleep(POLL_INTERVAL_SEC)


# ============================================================
# HTTP server
# ============================================================
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>Agent Witness</title>
<script src="https://cdn.jsdelivr.net/npm/cytoscape@3.34.0/dist/cytoscape.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>
<style>
  :root {
    --bg: #0a0b0f; --panel: #12141a; --panel-2: #1a1d26;
    --border: #242833; --border-soft: rgba(255,255,255,0.06);
    --text: #e8eaf0; --dim: #6b7180; --dim-2: #9098a8;
    --claude: #e8896a; --codex: #8b95ff; --hermes: #6ed8a3;
    --opencode: #c896ff; --omp: #f0c456;
    --live: #6ed8a3; --idle: #f0c456; --completed: #8b94a8;
    --stale: #4a4f5e; --error: #ee5e6a;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; overflow: hidden; }
  body {
    background: var(--bg); color: var(--text);
    font-family: 'SF Mono', ui-monospace, Menlo, Consolas, monospace;
    font-size: 12px; line-height: 1.5; -webkit-font-smoothing: antialiased;
  }
  header {
    height: 44px; padding: 0 16px;
    border-bottom: 1px solid var(--border); background: var(--panel);
    display: flex; align-items: center; gap: 18px;
  }
  header h1 {
    margin: 0; font-size: 12px; font-weight: 600;
    letter-spacing: 1.5px; color: var(--text);
  }
  .heartbeat {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--live); box-shadow: 0 0 0 0 rgba(110,216,163,0.7);
  }
  .heartbeat.beat { animation: heartbeat 0.8s ease-out; }
  @keyframes heartbeat {
    0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(110,216,163,0.6); }
    50% { transform: scale(1.5); box-shadow: 0 0 0 6px rgba(110,216,163,0); }
    100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(110,216,163,0); }
  }
  .stats { display: flex; gap: 18px; font-size: 11px; color: var(--dim); }
  .stat { display: flex; align-items: baseline; gap: 5px; }
  .stat .v { color: var(--text); font-weight: 600; font-size: 13px; font-variant-numeric: tabular-nums; }
  .stat .v.cc { color: var(--claude); }
  .stat .v.cx { color: var(--codex); }
  .stat .v.hm { color: var(--hermes); }
  .stat .v.oc { color: var(--opencode); }
  .stat .v.op { color: var(--omp); }
  .stat .v.live { color: var(--live); }
  main {
    display: grid;
    grid-template-columns: 380px 1fr 380px;
    gap: 1px; background: var(--border);
    height: calc(100vh - 44px);
  }
  .panel { background: var(--bg); display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .panel-header {
    padding: 10px 14px 8px; background: var(--panel);
    border-bottom: 1px solid var(--border-soft);
    display: flex; align-items: center; gap: 10px;
  }
  .panel-title {
    font-size: 10px; font-weight: 600; letter-spacing: 1.2px;
    color: var(--dim-2); text-transform: uppercase;
  }
  .panel-body { flex: 1; overflow-y: auto; padding: 6px 0; }
  .panel-body::-webkit-scrollbar { width: 6px; }
  .panel-body::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
  .chips { display: flex; gap: 4px; flex-wrap: wrap; padding: 8px 14px; border-bottom: 1px solid var(--border-soft); background: var(--panel); }
  .chip {
    font-size: 10px; padding: 3px 7px; border-radius: 3px;
    background: var(--panel-2); cursor: pointer; user-select: none;
    border: 1px solid transparent; color: var(--dim);
    transition: all 0.1s;
  }
  .chip:hover { background: var(--border); }
  .chip.active { color: var(--text); background: var(--border); }
  .chip.cc.active { color: var(--claude); border-color: var(--claude); }
  .chip.cx.active { color: var(--codex); border-color: var(--codex); }
  .chip.hm.active { color: var(--hermes); border-color: var(--hermes); }
  .chip.oc.active { color: var(--opencode); border-color: var(--opencode); }
  .chip.op.active { color: var(--omp); border-color: var(--omp); }
  .run-card {
    padding: 8px 14px 9px;
    border-left: 2px solid transparent;
    cursor: pointer; transition: background 0.12s;
  }
  .run-card:hover { background: var(--panel-2); }
  .run-card.cc { border-left-color: var(--claude); }
  .run-card.cx { border-left-color: var(--codex); }
  .run-card.hm { border-left-color: var(--hermes); }
  .run-card.oc { border-left-color: var(--opencode); }
  .run-card.op { border-left-color: var(--omp); }
  .run-card.highlighted { background: var(--panel-2); box-shadow: inset 0 0 0 1px rgba(255,255,255,0.1); }
  .run-card.flash { animation: cardflash 1.2s ease-out; }
  @keyframes cardflash {
    0%, 25% { background: rgba(110,216,163,0.10); }
    100% { background: transparent; }
  }
  .run-row { display: flex; align-items: center; gap: 8px; }
  .run-status { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
  .run-status.live { background: var(--live); animation: pulse-dot 1.5s ease-in-out infinite; }
  .run-status.idle { background: var(--idle); }
  .run-status.completed { background: var(--completed); }
  .run-status.stale { background: var(--stale); }
  @keyframes pulse-dot {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(1.4); }
  }
  .run-title {
    flex: 1; font-size: 12px; color: var(--text); font-weight: 500;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .run-tag {
    font-size: 9px; padding: 1px 5px; border-radius: 2px;
    color: var(--dim-2); letter-spacing: 0.5px;
    text-transform: uppercase; flex-shrink: 0;
    background: rgba(255,255,255,0.04);
  }
  .run-age { font-size: 10px; color: var(--dim); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .run-meta {
    font-size: 10px; color: var(--dim);
    margin-top: 3px; display: flex; gap: 10px;
    white-space: nowrap; overflow: hidden;
  }
  .run-meta .cwd { overflow: hidden; text-overflow: ellipsis; flex: 1; min-width: 0; }
  .run-meta .metrics { color: var(--dim-2); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .event {
    padding: 4px 14px; font-size: 11px;
    display: grid; grid-template-columns: 56px 18px 1fr;
    gap: 8px; align-items: baseline;
    border-left: 2px solid transparent;
  }
  .event.cc { border-left-color: var(--claude); }
  .event.cx { border-left-color: var(--codex); }
  .event.hm { border-left-color: var(--hermes); }
  .event.oc { border-left-color: var(--opencode); }
  .event.op { border-left-color: var(--omp); }
  .event.fresh { animation: slidein 0.5s cubic-bezier(0.2, 0.6, 0.2, 1); }
  @keyframes slidein {
    0% { opacity: 0; transform: translateX(-10px); background: rgba(110,216,163,0.18); }
    50% { background: rgba(110,216,163,0.08); }
    100% { opacity: 1; transform: translateX(0); background: transparent; }
  }
  .event .t { color: var(--dim); font-size: 10px; font-variant-numeric: tabular-nums; }
  .event .i { color: var(--dim-2); text-align: center; font-size: 11px; }
  .event .b { min-width: 0; overflow: hidden; }
  .event .b code {
    color: var(--codex); font-family: inherit; font-weight: 500;
    background: rgba(139,149,255,0.10); padding: 1px 5px;
    border-radius: 3px; font-size: 11px;
  }
  .event .b .arg {
    color: var(--dim-2); margin-left: 6px;
    overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; display: inline-block;
    max-width: calc(100% - 80px); vertical-align: bottom;
  }
  .event .b .ok { color: var(--live); }
  .event .b .err { color: var(--error); }
  .event .b .think { color: var(--dim-2); font-style: italic; }
  .event .b .msg { color: var(--text); }
  .event .b .truncate {
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    display: inline-block; max-width: 100%; vertical-align: bottom;
  }
  .empty {
    text-align: center; color: var(--dim); padding: 30px 14px;
    font-size: 11px; line-height: 1.7;
  }
  .empty b { color: var(--dim-2); }
  .topo-controls { padding: 6px 14px; display: flex; gap: 6px; align-items: center; font-size: 10px; color: var(--dim); border-bottom: 1px solid var(--border-soft); background: var(--panel); }
  .topo-stats { margin-left: auto; font-variant-numeric: tabular-nums; }
  #topo-canvas {
    flex: 1; width: 100%; min-height: 200px;
    background: radial-gradient(ellipse at center, #161821 0%, #0a0b0f 100%);
  }
  .topo-legend {
    padding: 7px 14px 8px; font-size: 10px; color: var(--dim);
    display: flex; gap: 14px; flex-wrap: wrap;
    border-top: 1px solid var(--border-soft); background: var(--panel);
  }
  .topo-legend .sw {
    display: inline-block; width: 7px; height: 7px;
    border-radius: 50%; margin-right: 5px; vertical-align: middle;
  }
  .topo-legend .ln {
    display: inline-block; width: 14px; margin-right: 5px;
    vertical-align: middle; border-top: 1.5px solid;
  }
</style>
</head>
<body>
<header>
  <span id="heartbeat" class="heartbeat" title="poll heartbeat"></span>
  <h1>AGENT WITNESS</h1>
  <div class="stats">
    <span class="stat">RUNS<span class="v" id="s-runs">—</span></span>
    <span class="stat">CC<span class="v cc" id="s-cc">—</span></span>
    <span class="stat">CX<span class="v cx" id="s-cx">—</span></span>
    <span class="stat">HM<span class="v hm" id="s-hm">—</span></span>
    <span class="stat">OC<span class="v oc" id="s-oc">—</span></span>
    <span class="stat">OMP<span class="v op" id="s-op">—</span></span>
    <span class="stat">LIVE<span class="v live" id="s-live">—</span></span>
    <span class="stat">EVT<span class="v" id="s-evt">—</span></span>
  </div>
  <div style="margin-left:auto; color: var(--dim); font-size: 10px;" id="last-poll">—</div>
</header>
<main>
  <div class="panel">
    <div class="panel-header"><span class="panel-title">runs</span></div>
    <div class="chips">
      <span class="chip cc active" data-src="claude-code">claude</span>
      <span class="chip cx active" data-src="codex">codex</span>
      <span class="chip hm active" data-src="hermes">hermes</span>
      <span class="chip oc active" data-src="opencode">opencode</span>
      <span class="chip op active" data-src="omp">omp</span>
      <span style="width:8px"></span>
      <span class="chip active" data-status="live">live</span>
      <span class="chip active" data-status="idle">idle</span>
      <span class="chip" data-status="stale">stale</span>
      <span class="chip active" data-status="completed">done</span>
    </div>
    <div class="panel-body" id="runs"></div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title">live event stream</span>
      <span style="margin-left:auto; font-size:10px; color:var(--dim)" id="evt-count">—</span>
    </div>
    <div class="panel-body" id="events"></div>
  </div>
  <div class="panel">
    <div class="panel-header">
      <span class="panel-title">topology</span>
      <span style="margin-left:auto; font-size:10px; color:var(--dim)" id="topo-stats">—</span>
    </div>
    <div id="topo-canvas"></div>
    <div class="topo-legend">
      <span><span class="sw" style="background:var(--claude)"></span>claude</span>
      <span><span class="sw" style="background:var(--codex)"></span>codex</span>
      <span><span class="sw" style="background:var(--hermes)"></span>hermes</span>
      <span><span class="sw" style="background:var(--opencode)"></span>opencode</span>
      <span><span class="sw" style="background:var(--omp)"></span>omp</span>
      <span style="margin-left:6px;color:var(--border)">|</span>
      <span><span class="ln" style="border-top-color:var(--hermes)"></span>continuation</span>
      <span><span class="ln" style="border-top-style:dashed;border-top-color:var(--idle)"></span>subagent</span>
    </div>
  </div>
</main>
<script>
'use strict';
const COLORS = { 'claude-code': '#e8896a', 'codex': '#8b95ff', 'hermes': '#6ed8a3', 'opencode': '#c896ff', 'omp': '#f0c456' };
const SHORT = { 'claude-code': 'cc', 'codex': 'cx', 'hermes': 'hm', 'opencode': 'oc', 'omp': 'op' };
const activeSrc = new Set(['claude-code','codex','hermes','opencode','omp']);
const activeStatus = new Set(['live','idle','completed']);
let highlightedRunId = null;
let lastSnap = null;
const seenEventIds = new Set();
const seenRunLastEvent = new Map();
// Hashes for diff-rendering — skip DOM rebuild when nothing changed
let lastRunsHash = '';
let lastEventsHash = '';
let lastTopoHash = '';
let renderInFlight = false;

function fnv1a(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h.toString(36);
}

function $(id){ return document.getElementById(id); }
function escHtml(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":"&#39;"}[c])); }
function fmtTime(ts){ const d = new Date(ts*1000); return d.toLocaleTimeString('en-GB', {hour12:false}); }
function fmtAge(s){
  if (s < 60) return Math.round(s)+'s';
  if (s < 3600) return Math.round(s/60)+'m';
  if (s < 86400) return (s/3600).toFixed(1)+'h';
  return (s/86400).toFixed(1)+'d';
}
function fmtTok(n){ return n >= 1000 ? (n/1000).toFixed(1)+'k' : String(n); }

function prettyToolCall(toolName, inputPreview) {
  const raw = inputPreview || '';
  let parsed = null;
  try { parsed = JSON.parse(raw); } catch {}
  const k = (toolName || '').toLowerCase();
  if (parsed && typeof parsed === 'object') {
    if (k === 'bash' || k === 'terminal' || k === 'exec_command') {
      return { tool: toolName, arg: '$ ' + (parsed.command || parsed.cmd || '') };
    }
    if (k === 'read' || k === 'read_file') return { tool: 'Read', arg: parsed.path || parsed.file_path || '' };
    if (k === 'edit' || k === 'patch' || k === 'multiedit') return { tool: toolName, arg: parsed.path || parsed.file_path || '' };
    if (k === 'write' || k === 'write_file') return { tool: 'Write', arg: parsed.path || parsed.file_path || '' };
    if (k === 'webfetch' || k === 'web_fetch' || k === 'browser_navigate') return { tool: toolName, arg: parsed.url || '' };
    if (k === 'websearch' || k === 'web_search') return { tool: 'Search', arg: parsed.query || '' };
    if (k === 'grep' || k === 'search_files') return { tool: 'Search', arg: parsed.pattern || parsed.query || '' };
    if (k === 'glob') return { tool: 'Glob', arg: parsed.pattern || '' };
    if (k === 'task' || k === 'delegate_task') return { tool: 'Spawn', arg: (parsed.description || parsed.goal || '').slice(0,80) };
    for (const [kk, vv] of Object.entries(parsed)) {
      if (typeof vv === 'string' && vv && vv.length < 200) return { tool: toolName, arg: kk + '=' + vv };
    }
  }
  return { tool: toolName, arg: raw.slice(0, 100) };
}

document.addEventListener('click', (e) => {
  const c = e.target.closest('.chip');
  if (!c) return;
  if (c.dataset.src) {
    c.classList.toggle('active');
    if (activeSrc.has(c.dataset.src)) activeSrc.delete(c.dataset.src);
    else activeSrc.add(c.dataset.src);
  } else if (c.dataset.status) {
    c.classList.toggle('active');
    if (activeStatus.has(c.dataset.status)) activeStatus.delete(c.dataset.status);
    else activeStatus.add(c.dataset.status);
  }
  if (lastSnap) render(lastSnap);
});

function render(snap) {
  lastSnap = snap;
  const hb = $('heartbeat');
  hb.classList.remove('beat');
  void hb.offsetWidth;
  hb.classList.add('beat');
  const by = { 'claude-code': 0, 'codex': 0, 'hermes': 0, 'opencode': 0, 'omp': 0 };
  snap.runs.forEach(r => { if (by.hasOwnProperty(r.source)) by[r.source]++; });
  $('s-runs').textContent = snap.stats.totalRuns;
  $('s-cc').textContent = by['claude-code'];
  $('s-cx').textContent = by['codex'];
  $('s-hm').textContent = by['hermes'];
  $('s-oc').textContent = by['opencode'];
  $('s-op').textContent = by['omp'];
  $('s-live').textContent = snap.stats.liveRuns;
  $('s-evt').textContent = snap.stats.totalEventsRetained;
  $('last-poll').textContent = 'last poll ' + fmtTime(snap.stats.lastPollAt);
  renderRuns(snap);
  renderEvents(snap);
  renderTopology(snap);
}

function renderRuns(snap) {
  const root = $('runs');
  const runs = snap.runs.filter(r => activeSrc.has(r.source) && activeStatus.has(r.status)).slice(0, 100);
  if (!runs.length) {
    if (lastRunsHash !== 'empty') {
      root.innerHTML = '<div class="empty">no runs match filters<br><b>tip</b>: toggle <code>stale</code> to see history</div>';
      lastRunsHash = 'empty';
    }
    return;
  }
  // Hash from id + lastEventAt + status + filter signature — only changes when something material moves
  const filterSig = [...activeSrc].sort().join(',') + '|' + [...activeStatus].sort().join(',');
  const sig = filterSig + '|' + (highlightedRunId || '') + '|' + runs.map(r => r.id+':'+r.lastEventAt+':'+r.status).join(';');
  const hash = fnv1a(sig);
  if (hash === lastRunsHash) return;   // ← short-circuit: nothing material changed
  lastRunsHash = hash;

  const flashes = new Set();
  runs.forEach(r => {
    const prev = seenRunLastEvent.get(r.id);
    if (prev !== undefined && r.lastEventAt > prev + 0.001) flashes.add(r.id);
    seenRunLastEvent.set(r.id, r.lastEventAt);
  });
  root.innerHTML = runs.map(r => {
    const m = r.metrics || {};
    const cwd = (r.workspace?.cwd || '').replace(/^\/Users\/[^/]+/, '~');
    const tag = SHORT[r.source] || '?';
    const flash = flashes.has(r.id) ? ' flash' : '';
    const hl = r.id === highlightedRunId ? ' highlighted' : '';
    const tokIn = m.inputTokens ? '↓'+fmtTok(m.inputTokens) : '';
    const tokOut = m.outputTokens ? '↑'+fmtTok(m.outputTokens) : '';
    const tools = m.toolCallCount ? '🔧'+m.toolCallCount : '';
    const cost = m.estimatedCostUsd ? '$'+m.estimatedCostUsd.toFixed(2) : '';
    const meta = [tools, tokIn, tokOut, cost].filter(Boolean).join('  ');
    return `<div class="run-card ${tag}${flash}${hl}" data-runid="${escHtml(r.id)}">
      <div class="run-row">
        <span class="run-status ${r.status}"></span>
        <span class="run-tag">${tag}</span>
        <span class="run-title">${escHtml(r.title || '?')}</span>
        <span class="run-age">${fmtAge(r.ageSec)}</span>
      </div>
      <div class="run-meta">
        <span class="cwd">${escHtml(cwd || '?')}</span>
        ${meta ? `<span class="metrics">${meta}</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

function renderEvents(snap) {
  const root = $('events');
  // Filter by source AND by selected run (if any)
  let events = snap.events.filter(e => activeSrc.has(e.source));
  if (highlightedRunId) {
    events = events.filter(e => e.runId === highlightedRunId);
  }

  // Header: show count + selection indicator
  const evtCount = $('evt-count');
  if (highlightedRunId) {
    const selectedRun = snap.runs.find(r => r.id === highlightedRunId);
    const title = (selectedRun?.title || highlightedRunId).slice(0, 28);
    const src = selectedRun ? SHORT[selectedRun.source] || '?' : '?';
    evtCount.innerHTML = `<span style="color:var(--idle)">● filtered: <b style="color:var(--text)">${escHtml(title)}</b> <span style="opacity:0.6">[${src}]</span></span> · <a href="#" id="clear-filter" style="color:var(--codex);text-decoration:none">clear ×</a>`;
    // wire clear button
    setTimeout(() => {
      const cb = document.getElementById('clear-filter');
      if (cb) cb.onclick = (ev) => {
        ev.preventDefault();
        highlightedRunId = null;
        lastRunsHash = ''; lastEventsHash = ''; lastTopoHash = '';
        if (lastSnap) render(lastSnap);
      };
    }, 0);
  } else {
    evtCount.textContent = events.length + ' events · newest first';
  }

  if (!events.length) {
    const msg = highlightedRunId
      ? `<div class="empty">no events in ring buffer for this run<br><b>tip</b>: events flow in as the run produces them — wait for new activity, or this run was idle in the last ${500} events</div>`
      : '<div class="empty">no recent events<br><b>tip</b>: open Claude / Codex / Hermes — events appear here within 1s</div>';
    if (lastEventsHash !== 'empty:' + (highlightedRunId || '')) {
      root.innerHTML = msg;
      lastEventsHash = 'empty:' + (highlightedRunId || '');
    }
    return;
  }
  // Hash from event ids + highlight selection
  const visible = events.slice(0, 80);
  const filterSig = [...activeSrc].sort().join(',') + '|sel:' + (highlightedRunId || '');
  const sig = filterSig + '|' + visible.map(e => e.eventId).join(';');
  const hash = fnv1a(sig);
  if (hash === lastEventsHash) return;
  lastEventsHash = hash;

  const fresh = new Set();
  events.forEach(e => {
    if (!seenEventIds.has(e.eventId)) { fresh.add(e.eventId); seenEventIds.add(e.eventId); }
  });
  if (seenEventIds.size > 2000) {
    const keep = new Set(events.map(e => e.eventId));
    seenEventIds.clear();
    keep.forEach(id => seenEventIds.add(id));
  }
  root.innerHTML = visible.map(e => {
    const tag = SHORT[e.source] || '?';
    const isFresh = fresh.has(e.eventId) ? ' fresh' : '';
    let body;
    if (e.kind === 'step') {
      const s = e.step;
      if (s.type === 'tool_call') {
        const p = prettyToolCall(s.toolName, s.inputPreview);
        body = `<span class="i">🔧</span><span class="b"><code>${escHtml(p.tool)}</code><span class="arg">${escHtml(p.arg)}</span></span>`;
      } else if (s.type === 'tool_result') {
        const cls = s.ok ? 'ok' : 'err';
        const icon = s.ok ? '✓' : '✗';
        body = `<span class="i ${cls}">${icon}</span><span class="b ${cls} truncate">${escHtml(s.outputPreview || '(no output)')}</span>`;
      } else if (s.type === 'thinking') {
        const lock = s.encrypted ? '🔒 ' : '';
        body = `<span class="i">💭</span><span class="b think truncate">${lock}${escHtml(s.text || '(thinking)')}</span>`;
      } else if (s.type === 'message') {
        const role = s.role === 'user' ? '👤' : '💬';
        body = `<span class="i">${role}</span><span class="b msg truncate">${escHtml(s.text || '')}</span>`;
      } else {
        body = `<span class="i">·</span><span class="b">${escHtml(s.type)}</span>`;
      }
    } else if (e.kind === 'metric_delta') {
      body = `<span class="i">📊</span><span class="b" style="color:var(--dim)">↓${e.data.in||0} ↑${e.data.out||0} tok</span>`;
    } else if (e.kind === 'turn_started') {
      body = `<span class="i">▶</span><span class="b" style="color:var(--dim-2)">turn started</span>`;
    } else {
      body = `<span class="i">·</span><span class="b">${escHtml(e.kind)}</span>`;
    }
    return `<div class="event ${tag}${isFresh}" title="${escHtml(e.runId)}">
      <span class="t">${fmtTime(e.timestamp)}</span>
      ${body}
    </div>`;
  }).join('');
}

// ========== Cytoscape topology renderer ==========
let cy = null;
let cyInitialized = false;
let cyKnownNodes = new Set();
let cyKnownEdges = new Set();
let cyLastFilterSig = '';

function initCytoscape() {
  if (cyInitialized) return;
  cyInitialized = true;
  cy = cytoscape({
    container: document.getElementById('topo-canvas'),
    wheelSensitivity: 0.2,
    minZoom: 0.3,
    maxZoom: 3,
    elements: [],
    style: [
      { selector: 'node', style: {
        'background-color': 'data(color)',
        'label': 'data(label)',
        'color': '#9098a8',
        'font-size': 9,
        'font-family': 'SF Mono, ui-monospace, monospace',
        'text-margin-y': -8,
        'text-halign': 'center',
        'text-valign': 'top',
        'width': 'data(size)',
        'height': 'data(size)',
        'border-width': 1.5,
        'border-color': '#0a0b0f',
        'overlay-opacity': 0,
        'opacity': 'data(opacity)',
      }},
      { selector: 'node[isOrphan = 1]', style: {
        'label': '', 'width': 10, 'height': 10, 'border-width': 1,
      }},
      { selector: 'node[isLive = 1]', style: {
        'border-color': '#6ed8a3', 'border-width': 2.5,
        'shadow-blur': 16, 'shadow-color': '#6ed8a3', 'shadow-opacity': 0.6,
      }},
      { selector: 'node:selected', style: {
        'border-color': '#fbbf24', 'border-width': 3,
        'shadow-blur': 20, 'shadow-color': '#fbbf24', 'shadow-opacity': 0.7,
      }},
      { selector: 'edge', style: {
        'width': 1.2,
        'line-color': '#5fd396',
        'curve-style': 'taxi',
        'taxi-direction': 'rightward',
        'taxi-turn': 18,
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#5fd396',
        'arrow-scale': 0.8,
        'opacity': 0.45,
      }},
      { selector: 'edge[kind = "subagent"]', style: {
        'line-color': '#f0c456',
        'target-arrow-color': '#f0c456',
        'line-style': 'dashed',
      }},
      { selector: 'edge[kind = "handoff"]', style: {
        'line-color': '#8b95ff',
        'target-arrow-color': '#8b95ff',
        'width': 2,
      }},
    ],
    layout: { name: 'preset' },
  });
  cy.on('tap', 'node', (e) => {
    const id = e.target.id();
    highlightedRunId = (highlightedRunId === id) ? null : id;
    if (highlightedRunId) {
      cy.animate({ center: { eles: e.target }, zoom: 1.4 }, { duration: 350 });
      const card = document.querySelector(`.run-card[data-runid="${CSS.escape(id)}"]`);
      if (card) card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
    // Force all 3 panels to re-render so events filter sees the selection
    if (lastSnap) { lastRunsHash = ''; lastEventsHash = ''; lastTopoHash = ''; render(lastSnap); }
  });
}

function relayoutCy() {
  if (!cy) return;
  const connected = cy.nodes('[isOrphan = 0]');
  const orphans = cy.nodes('[isOrphan = 1]');
  const containerW = cy.container().clientWidth;
  const containerH = cy.container().clientHeight;

  // Walk connected components — layout each tree separately, pack into a row-wise grid
  if (connected.length > 0) {
    const components = [];
    const seen = new Set();
    connected.forEach(n => {
      if (seen.has(n.id())) return;
      const comp = n.closedNeighborhood().union(n.successors()).union(n.predecessors());
      const compNodes = comp.nodes('[isOrphan = 0]');
      compNodes.forEach(cn => seen.add(cn.id()));
      if (compNodes.length > 0) components.push(compNodes);
    });
    // Sort: bigger components first
    components.sort((a, b) => b.length - a.length);

    // Layout each component, collect their bounding boxes
    const laid = components.map(comp => {
      // Use TB (top-to-bottom) when component has fan-out (wider than deep),
      // LR (left-to-right) when it's a linear chain
      const nodeCount = comp.length;
      // Heuristic: if any node has >2 children, use TB so siblings spread horizontally
      let useTB = false;
      comp.forEach(n => {
        if (n.outgoers('node').length >= 3) useTB = true;
      });
      comp.layout({
        name: 'dagre',
        rankDir: useTB ? 'TB' : 'LR',
        nodeSep: 18,
        rankSep: useTB ? 45 : 55,
        edgeSep: 8,
        animate: false,
        fit: false,
      }).run();
      return { comp, bb: comp.boundingBox(), useTB };
    });

    // Pack components left-to-right, wrapping to next row when overflow
    const PADDING = 20;
    const COMP_GAP = 28;
    let curX = PADDING, curY = PADDING, rowMaxH = 0;
    const maxRowW = Math.max(containerW - PADDING * 2, 400);
    laid.forEach(({ comp, bb }) => {
      if (curX > PADDING && curX + bb.w > maxRowW) {
        // Wrap to next row
        curX = PADDING;
        curY += rowMaxH + COMP_GAP;
        rowMaxH = 0;
      }
      const dx = curX - bb.x1;
      const dy = curY - bb.y1;
      comp.forEach(n => {
        const p = n.position();
        n.position({ x: p.x + dx, y: p.y + dy });
      });
      curX += bb.w + COMP_GAP;
      if (bb.h > rowMaxH) rowMaxH = bb.h;
    });
  }

  // Orphan grid below trees
  const cBounds = connected.length > 0 ? connected.boundingBox() : { x1: 10, y1: 10, x2: 10, y2: 10, w: 0, h: 0 };
  if (orphans.length > 0) {
    const startX = 14;
    const startY = (connected.length > 0 ? cBounds.y2 + 40 : 20);
    const spacing = 14;
    const cols = Math.max(14, Math.floor((containerW - 28) / spacing));
    orphans.forEach((n, i) => {
      const col = i % cols, row = Math.floor(i / cols);
      n.position({ x: startX + col * spacing, y: startY + row * spacing });
    });
  }

  // Fit smartly: cap zoom so it doesn't get tiny
  cy.fit(undefined, 20);
  const z = cy.zoom();
  if (z > 1.0) cy.zoom(1.0);
  if (z < 0.6) cy.zoom(0.6);
}

function renderTopology(snap) {
  if (!cy) initCytoscape();
  const statsEl = document.getElementById('topo-stats');
  const runMap = new Map(snap.runs.map(r => [r.id, r]));
  const visibleRuns = snap.runs.filter(r => activeSrc.has(r.source) && activeStatus.has(r.status));
  const visibleSet = new Set(visibleRuns.map(r => r.id));
  const edges = snap.topology.filter(e =>
    visibleSet.has(e.parentRunId) || visibleSet.has(e.childRunId)
  );
  const nodesInEdges = new Set();
  edges.forEach(e => { nodesInEdges.add(e.parentRunId); nodesInEdges.add(e.childRunId); });

  statsEl.textContent = `${snap.stats.totalTopologyEdges} edges · ${visibleRuns.length} runs`;

  // Hash to decide whether to re-layout
  const filterSig = [...activeSrc].sort().join(',') + '|' + [...activeStatus].sort().join(',');
  const structureSig = filterSig + '|' + visibleRuns.map(r => r.id).join(';') + '|' + edges.map(e => e.parentRunId+'>'+e.childRunId).join(';');
  const structureHash = fnv1a(structureSig);
  const structureChanged = (structureHash !== lastTopoHash);
  lastTopoHash = structureHash;

  // Determine target node set (cap to keep cy snappy)
  const TARGET_NODES = new Map();
  // Always include nodes in edges (forced visibility)
  edges.forEach(e => {
    if (runMap.has(e.parentRunId) && activeSrc.has(runMap.get(e.parentRunId).source))
      TARGET_NODES.set(e.parentRunId, runMap.get(e.parentRunId));
    if (runMap.has(e.childRunId) && activeSrc.has(runMap.get(e.childRunId).source))
      TARGET_NODES.set(e.childRunId, runMap.get(e.childRunId));
  });
  // Add orphans up to a budget (avoid 500-orphan flood)
  const ORPHAN_BUDGET = 60;
  let orphanCount = 0;
  for (const r of visibleRuns) {
    if (nodesInEdges.has(r.id)) continue;
    if (orphanCount >= ORPHAN_BUDGET) break;
    TARGET_NODES.set(r.id, r);
    orphanCount++;
  }
  const targetIds = new Set(TARGET_NODES.keys());
  const targetEdgeIds = new Set(edges
    .filter(e => targetIds.has(e.parentRunId) && targetIds.has(e.childRunId))
    .map(e => e.parentRunId + '>>' + e.childRunId));

  cy.startBatch();

  // Remove gone nodes
  cyKnownNodes.forEach(id => {
    if (!targetIds.has(id)) {
      const n = cy.getElementById(id);
      if (n.length) n.remove();
    }
  });
  // Remove gone edges
  cyKnownEdges.forEach(id => {
    if (!targetEdgeIds.has(id)) {
      const e = cy.getElementById(id);
      if (e.length) e.remove();
    }
  });

    // Add/update nodes
  targetIds.forEach(id => {
    const r = TARGET_NODES.get(id);
    const isLive = r.status === 'live';
    const isOrphan = !nodesInEdges.has(id);
    const sz = isOrphan ? 9 : (isLive ? 24 : 18);
    const op = r.status === 'stale' ? 0.45 : (r.status === 'completed' ? 0.85 : 1.0);
    const label = (r.title || '?').slice(0, isOrphan ? 0 : 13);
    const data = {
      id,
      label,
      color: COLORS[r.source] || '#888',
      size: sz,
      opacity: op,
      isLive: isLive ? 1 : 0,
      isOrphan: isOrphan ? 1 : 0,
      source: r.source,
      title: r.title || '?',
    };
    if (cyKnownNodes.has(id)) {
      const n = cy.getElementById(id);
      n.data(data);
    } else {
      cy.add({ group: 'nodes', data });
    }
  });

  // Add new edges
  targetEdgeIds.forEach(eid => {
    if (cyKnownEdges.has(eid)) return;
    const [src, tgt] = eid.split('>>');
    const e = edges.find(x => x.parentRunId === src && x.childRunId === tgt);
    cy.add({ group: 'edges', data: { id: eid, source: src, target: tgt, kind: e.kind } });
  });

  cyKnownNodes = targetIds;
  cyKnownEdges = targetEdgeIds;

  // Apply highlight via selection
  cy.nodes(':selected').unselect();
  if (highlightedRunId && cy.getElementById(highlightedRunId).length) {
    cy.getElementById(highlightedRunId).select();
  }

  cy.endBatch();

  // Re-layout only when structure changed OR filter changed
  if (structureChanged || filterSig !== cyLastFilterSig) {
    cyLastFilterSig = filterSig;
    relayoutCy();
  }
}

async function tick() {
  if (renderInFlight) return;       // ← guard against pile-up
  renderInFlight = true;
  try {
    const r = await fetch('/api/snapshot', { cache: 'no-store' });
    const j = await r.json();
    render(j);
  } catch (e) {
    console.error('tick error:', e);
  } finally {
    renderInFlight = false;
  }
}

// Event delegation: one listener for ALL run cards (instead of N listeners)
document.getElementById('runs').addEventListener('click', (e) => {
  const card = e.target.closest('.run-card');
  if (!card) return;
  const id = card.dataset.runid;
  highlightedRunId = (highlightedRunId === id) ? null : id;
  // Also focus the topology graph on the corresponding node
  if (cy && highlightedRunId) {
    const node = cy.getElementById(highlightedRunId);
    if (node.length) cy.animate({ center: { eles: node }, zoom: 1.4 }, { duration: 350 });
  }
  // Force re-render of all panels so events filter updates immediately
  if (lastSnap) { lastRunsHash = ''; lastEventsHash = ''; lastTopoHash = ''; render(lastSnap); }
});

tick();
setInterval(tick, 1500);   // 1.5s — backend polls at 1s, faster front polls give nothing
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args, **kwargs): pass  # silence

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8"))
        elif self.path == "/api/snapshot":
            data = json.dumps(STATE.snapshot(), default=str, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", data)
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve():
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[witness] serving http://127.0.0.1:{PORT}")
    httpd.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=poller_loop, daemon=True).start()
    serve()
