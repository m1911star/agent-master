# 04 · Frontend Stack Selection & Interaction Design

## Graph library selection (measured comparison)

The user's feedback was "rendering looks rough" — the hand-rolled SVG elbow paths looked like 1990s graphviz. A subtask did a real 200-node benchmark:

| Library | OOTB beauty | Single-file CDN | 200-node perf | Live updates | Bundle size |
|---|---|---|---|---|---|
| **Cytoscape.js + cytoscape-dagre** ✅ | medium (15 lines of styling) | ✅ UMD | **24 ms** | first-class (`cy.add/remove` O(n)) | 425 KB |
| G6 (AntV) | high | ✅ | good | first-class | 700 KB |
| vis-network | medium (physics flavor) | ✅ | 417 ms | jitters | 629 KB |
| D3 v7 | DIY | ✅ | 30–50ms but you write everything | manual | 280 KB |
| Sigma.js v3 | high | ❌ ESM only | excellent | first-class | n/a |
| React Flow | highest | ❌ needs React | good | first-class | n/a |
| dagre-d3 | 1990s look (same as my current issue) | UMD but 7 yrs stale | mediocre | none | 280 KB |

**Picked Cytoscape.js 3.34 + cytoscape-dagre 2.5 + dagre 0.8.5** for these reasons:

- Three `<script>` CDN tags, zero build
- 24ms for 200 nodes (4× under my 100ms budget)
- `taxi` curve-style gives you industrial-grade elbows (not hand-rolled SVG paths)
- Incremental add/remove at 0.9ms, no full re-layout
- Style API is data-driven (`'background-color': 'data(color)'`), saves ~50 lines of styling
- v3.34 actively released weekly, used by NDEx / GeneMANIA / Reactome (good bus factor)

**Pitfalls encountered**:

| Pitfall | Fix |
|---|---|
| `rankDir: 'LR'` "doesn't work" | It actually works — bug was fit() squashing multiple trees into a long column |
| Multiple trees jammed together and fit() shrinks them | Split by `connected components`, layout each separately, row-wise pack |
| Fan-out vs linear chain need different layouts | Heuristic: out-degree ≥3 use TB (children spread horizontally), else LR |
| Long node labels overlap | `text-margin-y: -8` + cap label length at 13 chars |
| Default mousewheel too sensitive | `wheelSensitivity: 0.2` |
| `cytoscape-dagre@4` requires ESM | Lock `@2.5.0` + `dagre@0.8.5` |
| Layout jumps on node add | Only relayout when structure changes; hash short-circuit |

## Render layer architecture

```
INDEX_HTML (one r-string)
  ├── <head>
  │     ├── 3 CDN scripts (cytoscape + dagre + cytoscape-dagre)
  │     └── ~250 lines CSS (dark theme + color vars + animation keyframes)
  └── <body>
        ├── <header> heartbeat dot + 6 category counters + last poll
        ├── <main grid-template-columns: 380px 1fr 380px>
        │     ├── Left: RUNS panel
        │     │     ├── chips (source × status dual-axis filter)
        │     │     └── card list (top 100 by lastEventAt)
        │     ├── Middle: LIVE EVENT STREAM panel
        │     │     ├── status bar (filter badge / clear ×)
        │     │     └── 80 events, sorted by timestamp DESC
        │     └── Right: TOPOLOGY panel
        │           ├── status bar (edges / runs counters)
        │           ├── #topo-canvas (Cytoscape container)
        │           └── legend at bottom
        └── <script>
              ├── fnv1a hash
              ├── activeSrc / activeStatus Sets
              ├── highlightedRunId state
              ├── render() → renderRuns / renderEvents / renderTopology
              ├── Cytoscape singleton + incremental diff
              └── tick() 1.5s loop + fetch guard
```

## Three-way linkage design

This is the interaction the user was most satisfied with: **click a run in any panel, the other two focus on it**.

```
┌─────────┐         ┌─────────┐         ┌─────────┐
│  RUNS   │ ←─────→ │  EVENTS │ ←─────→ │  TOPO   │
│         │   sync  │         │   sync  │         │
└────┬────┘         └─────────┘         └────┬────┘
     │                                        │
     └────────────── highlightedRunId ────────┘
```

**Implementation details**:

1. User clicks a RUNS card → `highlightedRunId = id`
   - RUNS card gets `.highlighted` class (dark background + inset border)
   - EVENTS filter to `e.runId === id`
   - TOPOLOGY calls `cy.animate({center: {eles: node}, zoom: 1.4})`
2. User clicks a TOPOLOGY node → same logic, reverse trigger
3. EVENTS shows yellow badge at top: `● filtered: <title> [oc] · clear ×`
4. Click `clear ×` → `highlightedRunId = null`, all three panels restore

**Key code**:

```js
function renderEvents(snap) {
  let events = snap.events.filter(e => activeSrc.has(e.source));
  if (highlightedRunId) {
    events = events.filter(e => e.runId === highlightedRunId);  // ← key
  }
  // Top filter badge
  if (highlightedRunId) {
    const selectedRun = snap.runs.find(r => r.id === highlightedRunId);
    evtCount.innerHTML = `<span style="color:var(--idle)">● filtered: ...</span>
                          · <a id="clear-filter">clear ×</a>`;
  }
  // ...
}
```

## Color palette & visual grammar

| Element | Color | Meaning |
|---|---|---|
| Claude Code | `#e8896a` orange-red | (Anthropic brand) |
| Codex | `#8b95ff` blue-purple | (OpenAI blue tone) |
| Hermes | `#6ed8a3` green | (Hermes brand) |
| OpenCode | `#c896ff` purple | |
| omp | `#f0c456` gold | (distinct yellow tag) |
| ─────────────── | ─────── | ─────── |
| live | same hermes green + shadow-blur | "running" |
| idle | same omp yellow | "waiting for user input" |
| completed | gray `#8b94a8` | "ended" |
| stale | dark gray `#4a4f5e` | "no activity > 10 min" |
| highlighted | gold `#fbbf24` border | "selected" |
| error | red `#ee5e6a` | tool_result failure |

**Edge kinds** (topology):

| kind | style |
|---|---|
| `continuation` | solid green (hermes color) |
| `subagent` | dashed yellow (idle color) |
| `handoff` | thick solid blue (codex color) |

## Key CSS techniques

```css
/* True monospace single-character width → digit alignment */
.run-age { font-variant-numeric: tabular-nums; }

/* Heartbeat pulse */
@keyframes heartbeat {
  0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(110,216,163,0.6); }
  50% { transform: scale(1.5); box-shadow: 0 0 0 6px rgba(110,216,163,0); }
  100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(110,216,163,0); }
}

/* New event entry animation */
.event.fresh { animation: slidein 0.5s cubic-bezier(0.2, 0.6, 0.2, 1); }
@keyframes slidein {
  0% { opacity: 0; transform: translateX(-10px); background: rgba(110,216,163,0.18); }
  100% { opacity: 1; transform: translateX(0); background: transparent; }
}

/* Live run card status dot breathing */
@keyframes pulse-dot {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(1.4); }
}
```

## Pretty tool call parser (signal-to-noise critical)

Earlier the event stream was a noise wall from `JSON.stringify(input)`. `prettyToolCall()` converts to "human language" per tool type:

```js
function prettyToolCall(toolName, inputPreview) {
  const parsed = JSON.parse(inputPreview);
  const k = toolName.toLowerCase();
  if (k === 'bash' || k === 'terminal') {
    return { tool: toolName, arg: '$ ' + parsed.command };
  }
  if (k === 'read' || k === 'edit' || k === 'write') {
    return { tool: toolName, arg: parsed.path || parsed.file_path };
  }
  if (k === 'webfetch' || k === 'browser_navigate') {
    return { tool: toolName, arg: parsed.url };
  }
  if (k === 'task' || k === 'delegate_task') {
    return { tool: 'Spawn', arg: parsed.description || parsed.goal };
  }
  // ... etc
}
```

Effect: `Bash {"command":"git status","timeout":300,"workdir":"/Users/..."}` becomes `Bash $ git status`.
