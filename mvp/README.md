# Agent Master — MVP Design & Implementation Notes

> A local-first "boss dashboard" that pulls every coding agent running on your machine (Claude Code / Codex / Hermes / OpenCode / omp) into one screen — live status, event stream, and parent/child topology.
>
> This directory captures the full design from the spike phase (2026-06-06) so future-you (and any collaborators / open source contributors) have an aligned source of truth.

## Document layout

| File | Content |
|---|---|
| `00-vision-and-positioning.md` | Product positioning, how market judgment evolved, why nobody owns this angle |
| `01-data-model.md` | Core abstraction: AgentRun / Turn / Step / AgentEvent / AgentAdapter (six-layer model) |
| `02-source-adapters.md` | Real data shapes of 5 sources (Claude Code, Codex, Hermes, OpenCode, omp) and adapter translation rules |
| `03-architecture.md` | Spike-stage architecture: collection layer / in-memory event bus / HTTP snapshot API / browser render layer |
| `04-frontend-stack.md` | Frontend stack selection (Cytoscape + dagre), hash short-circuit, three-way linkage |
| `05-known-gaps-and-next-steps.md` | Known issues + prioritized backlog |
| `06-conversation-log.md` | Iteration history of this session (narrative version) |

## Key decisions at a glance

- **Form factor**: single Python file (stdlib only) + inline HTML/JS + 3 CDN scripts (cytoscape, dagre, cytoscape-dagre). Zero npm / zero build.
- **Collection strategy**: poll each source's storage every 1s (SQLite tail / jsonl byte-offset), translate into unified `AgentEvent`, push into in-memory ring buffer.
- **Data model**: six layers (AgentRun / Turn / Step / AgentEvent / AgentAdapter / DashboardState). The `raw` field is deliberately kept so any missed source info has a fallback.
- **Topology algorithm**: dagre + per-component packing. Fan-out trees use TB, linear chains use LR.
- **Performance**: hash short-circuit + payload truncation (943 runs → send 200 → render 100). Near-zero DOM rebuild when idle.
- **Business model**: niche but the right category. Aligns with Raycast / Obsidian / Linear (developer-focused personal paid tools), NOT LangSmith-style enterprise SaaS.

## Where the current MVP code lives

Right next to this `mvp/` directory:

```
../witness.py   # ~1750 lines, single file
```

To run:

```bash
cd ~/sideproject/agent-master && python3 witness.py
# open http://127.0.0.1:8765 in your browser
```

When the MVP graduates to a real product, the design docs in `mvp/` get migrated to `docs/` in the production repo.
