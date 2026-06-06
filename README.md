# Agent Master

> A local-first "boss dashboard" for every coding agent on your machine.
> Watch Claude Code, Codex, Hermes, OpenCode, and omp side-by-side — live status, event stream, and parent/child topology in one screen.

![status](https://img.shields.io/badge/status-MVP%20spike-yellow)
![python](https://img.shields.io/badge/python-stdlib%20only-blue)
![deps](https://img.shields.io/badge/dependencies-zero%20npm%20%2F%20zero%20pip-green)

## Why

You probably run multiple coding agents — Claude Code in one terminal, Codex in another, Hermes proxying both, an OpenCode subagent fan-out elsewhere. Today there's no single place that shows you what they're all doing.

- **LangSmith / Helicone / Langfuse** are enterprise SaaS — wrong customer, wrong privacy model
- **Each agent's own dashboard** sees only its own sessions
- **Hermes Dashboard** is great, but only watches Hermes

This project fills the gap: **passively observe every agent on your local machine, normalize into one event stream, render as one dashboard.** No instrumentation needed. Data never leaves the machine.

## What you see

Three live panels, updated every 1.5 seconds:

| Panel | Content |
|---|---|
| **Runs** | One card per active/recent agent session — title, status, tokens, cost, tool count. Filter by source × status. |
| **Live Event Stream** | Tool calls (`Bash $ git status`), thinking, messages, tool results from every source — color-coded, newest first. |
| **Topology** | Parent → child trees rendered with Cytoscape + dagre. Continuation (Hermes), subagent (Claude / OpenCode), and handoff edges. |

Click any run in any panel → all three panels focus on it. Three-way linkage.

## Sources currently supported

| Source | Storage | Topology signal |
|---|---|---|
| **Claude Code** | `~/.claude/projects/*/*.jsonl` | `isSidechain` + `agentId` subagents |
| **Codex** | `~/.codex/sessions/YYYY/MM/DD/*.jsonl` | (linear, no subagents) |
| **Hermes** | `~/.hermes/state.db` (SQLite) | `parent_session_id` (continuation) |
| **OpenCode** | `~/.local/share/opencode/opencode.db` (SQLite) | `session.parent_id` (subagent) |
| **omp / oh-my-pi** | `~/.omp/agent/sessions/.../*.jsonl` | (linear) |

Adding a new source = ~80 lines of Python. See [`mvp/02-source-adapters.md`](./mvp/02-source-adapters.md) for the template.

## Run it

```bash
python3 witness.py
```

Then open http://127.0.0.1:8765

That's it. Zero install. Single file. Pure Python stdlib + three CDN scripts (cytoscape, dagre, cytoscape-dagre) for the graph.

> Requires Python 3.9+. Tested on macOS.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  poller_loop (1s tick)                                  │
│    ├── claude  ── jsonl byte-offset tail                │
│    ├── codex   ── jsonl byte-offset tail                │
│    ├── hermes  ── SQLite WHERE id > last                │
│    ├── opencode── SQLite WHERE time_created > last      │
│    └── omp     ── jsonl byte-offset tail                │
│            ↓ translate into unified AgentEvent          │
└─────────────────────────────────────────────────────────┘
                      ↓
            in-memory ring buffer (500 events)
                      ↓
            GET /api/snapshot (HTTP, 154KB JSON)
                      ↓ fetch every 1.5s
            browser (hash short-circuit, 0 DOM rebuild when idle)
```

Full design notes in [`mvp/`](./mvp/):

- [`00-vision-and-positioning.md`](./mvp/00-vision-and-positioning.md) — why this exists, market judgment
- [`01-data-model.md`](./mvp/01-data-model.md) — six-layer abstraction (the constitution)
- [`02-source-adapters.md`](./mvp/02-source-adapters.md) — every source's real data shape
- [`03-architecture.md`](./mvp/03-architecture.md) — collection / event bus / render layers + performance optimization
- [`04-frontend-stack.md`](./mvp/04-frontend-stack.md) — Cytoscape selection, three-way linkage
- [`05-known-gaps-and-next-steps.md`](./mvp/05-known-gaps-and-next-steps.md) — backlog
- [`06-conversation-log.md`](./mvp/06-conversation-log.md) — how this spike came to be

## Status

🟡 **MVP spike.** Single-file Python, runs locally, works end-to-end. Not packaged, not polished, no installer. Designed to validate the multi-source unification thesis. See [`mvp/05-known-gaps-and-next-steps.md`](./mvp/05-known-gaps-and-next-steps.md) for what's planned.

## Privacy

Everything stays on your machine. The dashboard binds to `127.0.0.1` only. Tool input/output (which can contain secrets, source code, private messages) is truncated to short previews in the UI and never sent anywhere. No telemetry, no analytics, no network calls except CDN fetches for the three JS libraries.

## License

TBD (probably MIT or Apache-2.0).
