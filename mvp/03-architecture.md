# 03 · Architecture & Performance Optimization

## Physical architecture (spike stage)

```
┌─────────────────────────────────────────────────────────────┐
│  Collection layer (Python main process, single polling thread)│
│                                                              │
│  poller_loop()  ticks every 1 second:                       │
│    ├── claude_adapter_tick()    glob jsonl + byte offset    │
│    ├── codex_adapter_tick()     glob jsonl + byte offset    │
│    ├── hermes_adapter_tick()    SQLite tail (WHERE id > ?)   │
│    ├── opencode_adapter_tick()  SQLite tail (time_created>?)│
│    └── omp_adapter_tick()       glob jsonl + byte offset    │
│         ↓ translate into unified AgentEvent                  │
│    STATE.add_event() / STATE.upsert_run() / add_topology... │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  In-memory event bus (State class, threading.Lock)           │
│  ├── runs: dict[str, AgentRun]            943 runs          │
│  ├── events: deque[dict] (maxlen=500)     ring buffer        │
│  ├── topology: list[TopologyEdge]         746 edges          │
│  └── file_offsets / hermes_last_msg_id... monotonic cursors │
└─────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│  HTTP snapshot API (ThreadingHTTPServer)                     │
│                                                              │
│  GET /api/snapshot                                           │
│    → snapshot(max_runs=200)                                  │
│       - top 200 runs by lastEventAt                          │
│       - topology localized (only edges between visible nodes)│
│       - returns 154KB JSON (down from 629KB → -76%)          │
└─────────────────────────────────────────────────────────────┘
                          ↓ HTTP fetch every 1.5s
┌─────────────────────────────────────────────────────────────┐
│  Browser render layer (single HTML, inline JS + 3 CDN script)│
│                                                              │
│  tick() → fetch → render()                                   │
│    ├── renderRuns()      ── hash short-circuit, 0 rebuild when unchanged
│    ├── renderEvents()    ── hash short-circuit + filter by selected run
│    └── renderTopology()  ── Cytoscape + dagre incremental update
│                                                              │
│  Three-way linkage: click any panel → other two focus       │
└─────────────────────────────────────────────────────────────┘
```

## Key design decisions

### 1. Single Python file + inline HTML

**Why not React / Vue / Svelte**:
- Spike's top priority: "single file, one-command run"
- Zero npm install / zero build step / zero venv
- User `cd` in and runs `python3 witness.py`
- When you later want to split, the HTML string can be cut out as `index.html` with no sunk cost

**Cost**: HTML/CSS/JS sit in one r-string — no IDE highlight/completion. ~600 lines of frontend in a Python string has a ceiling; before going to production this needs splitting.

### 2. Polling, not fsevents

**Why not watchdog / chokidar**:
- macOS fsevents measures at 1ms latency (Node chokidar), but needs native deps (pyobjc or chokidar)
- 5 sources × ~847 files × 1s polling = ~5000 `os.path.getsize()` per second; imperceptible on macOS (local stat is extremely fast)
- Simpler mental model: one loop, five functions, no callback hell

**Upgrade trigger**: 10+ sources or massive idle files → revisit fsevents.

### 3. In-memory ring buffer, no persistence

**Why events don't hit disk**:
- Spike doesn't need replay (v2 will)
- 500-line ring is enough for demo
- No persistence = restart re-derives state from sources, no schema maintenance

**Upgrade**: v2 adds `events.db` (SQLite) for event history; schema already defined in `01-data-model.md` TS section.

### 4. HTTP polling, not WebSocket / SSE

**Why not push**:
- 1.5s polling satisfies the "boss view" of real-time
- Python stdlib only has `http.server`; SSE/WS would need starlette / aiohttp
- HTTP polling is easy to debug (just curl the snapshot)
- Browser-side hash short-circuit ensures polling doesn't waste DOM rebuilds

**Upgrade trigger**: when the dashboard needs to show token-level streaming ("watch the AI type"), 1.5s is no longer enough.

## Performance optimization history

### The disaster before optimization

When the user reported "performance has issues", measured:

| Metric | Value | Root cause |
|---|---|---|
| API payload | **629 KB / sec** | 836 runs sent but only 100 rendered |
| Backend API latency | 6–10ms ✅ | OK |
| DOM rebuild | **836 cards + 858 SVG nodes rebuilt fully every second** | `innerHTML = bigString` |
| Frontend response | janky | browser keeps re-parsing/laying-out/painting |

### Three-strike fix

**1. Backend payload truncation** (154KB / -76%):

```python
def snapshot(self, max_runs: int = 200):
    runs_out.sort(key=lambda r: r.get("lastEventAt") ..., reverse=True)
    total_runs = len(runs_out)
    runs_out = runs_out[:max_runs]
    visible_run_ids = {r["id"] for r in runs_out}
    # Localize topology too
    topo_out = [e for e in self.topology
                if e["parentRunId"] in visible_run_ids
                or e["childRunId"] in visible_run_ids]
```

**2. Frontend hash short-circuit** (0 DOM rebuild when idle):

```js
function fnv1a(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h.toString(36);
}

function renderRuns(snap) {
  const sig = filterSig + '|' + runs.map(r => r.id+':'+r.lastEventAt).join(';');
  const hash = fnv1a(sig);
  if (hash === lastRunsHash) return;   // short-circuit
  lastRunsHash = hash;
  root.innerHTML = ...;
}
```

Measured: 0 DOM rebuilds during 8s of idle.

**3. Event delegation + fetch pile-up guard**:

```js
let renderInFlight = false;
async function tick() {
  if (renderInFlight) return;       // don't pile up while previous is in flight
  renderInFlight = true;
  try { ... } finally { renderInFlight = false; }
}

// 1 listener replaces N
document.getElementById('runs').addEventListener('click', (e) => {
  const card = e.target.closest('.run-card');
  if (card) { ... }
});
```

### After optimization

| Metric | Before | After | Improvement |
|---|---|---|---|
| API payload | 629 KB | 154 KB | -76% |
| Topology edges sent | 674 | 97 | -86% |
| Idle DOM rebuilds / 8s | ~5 full | 0 | ∞ |
| Forced full render | ~50–200ms? | 6ms median | 10×+ |
| Tick interval | 1000ms | 1500ms | backend ticks at 1s, no point fetching faster |

## On real-time tiers

When the user asked "isn't this just static parsing", I gave this clean taxonomy:

| Tier | Latency | Mechanism | Use case |
|---|---|---|---|
| **L1 state-level** | 1–3 sec | fsevents / polling + incremental parse | Boss view: "it's still running / stuck / done" |
| **L2 action-level** | sub-second | L1 + high-freq sources (Codex 14ms flush) | "Claude is running npm test right now" |
| **L3 token-level** | 10–50ms | Can't rely on logs: HTTP proxy / SDK push / IPC | "Watch the AI type" |

**Current MVP sits at L1.** L2 is effectively achieved for Codex/OpenCode (they flush fast); L3 needs a different route (proxy or active instrumentation).

## Module file inventory

Actual code (all in one file at spike stage):

```
~/sideproject/agent-witness-spike/
├── witness.py                # ~1750 lines, everything
│   ├── State class           # in-memory event bus
│   ├── claude_adapter_tick   # ~80 lines
│   ├── codex_adapter_tick    # ~70 lines
│   ├── hermes_adapter_tick   # ~70 lines
│   ├── opencode_adapter_tick # ~120 lines
│   ├── omp_adapter_tick      # ~80 lines
│   ├── poller_loop           # ~80 lines (incl. prime phase)
│   ├── Handler (HTTP)        # ~30 lines
│   └── INDEX_HTML            # ~580 lines inline HTML/JS/CSS
└── witness.py.bak.before-cytoscape   # backup from before the graph library swap
```

## Path to production

```
spike (now)
  ├─ single Python file + inline HTML
  └─ in-memory ring buffer, cleared on restart

  ↓ split

v0.1: file separation
  ├─ adapter/ directory, one file per source
  ├─ static/index.html + static/witness.js
  ├─ events.db on disk (SQLite, schema per §01)
  └─ still stdlib + http.server, zero npm

  ↓ upgrade UI

v1.0: real frontend project
  ├─ Vite + TS + Cytoscape (still)
  ├─ WebSocket / SSE replaces polling
  ├─ Backend → FastAPI (OpenAPI for typed clients)
  └─ Package as Tauri / Electron menubar app
```
