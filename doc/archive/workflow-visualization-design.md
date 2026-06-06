# Workflow Visualization — Design Document

## Layout Algorithm

### Choice: Dagre (default) + ELK (large workflows)

| Criterion | Dagre | ELK | Custom |
|-----------|-------|-----|--------|
| Bundle size | 28KB gzip | 180KB gzip | 0 |
| Cytoscape.js integration | `cytoscape-dagre` native | `cytoscape-elk` (worker) | manual |
| Compound nodes | ⚠️ basic | ✅ full support | manual |
| Incremental layout | ❌ full recompute | ✅ partial | manual |
| Performance <50 nodes | ✅ <5ms | ✅ <10ms | depends |
| Performance 100+ nodes | ⚠️ 50-200ms | ✅ <50ms (worker) | depends |
| Edge routing quality | good | excellent | poor |
| Layer constraints | basic | full | none |

**Decision**: Use **dagre** as default for workflows ≤50 nodes (covers 90% of cases). Switch to **ELK via Web Worker** for workflows >50 nodes or when compound node support is needed.

### Dagre Configuration

```typescript
const DAGRE_DEFAULTS: DagreLayoutOptions = {
  name: 'dagre',
  rankDir: 'TB',           // top-to-bottom (matches mental model of "progress flows down")
  nodeSep: 60,             // horizontal spacing between nodes in same rank
  rankSep: 80,             // vertical spacing between ranks
  edgeSep: 20,             // minimum edge separation
  ranker: 'network-simplex', // best rank assignment for most DAGs
  align: 'UL',            // align nodes upper-left in their rank
  acyclicer: 'greedy',    // handle any accidental cycles gracefully
  // Animation
  animate: true,
  animationDuration: 300,
  animationEasing: 'ease-in-out-sine',
  // Don't move nodes that have been manually positioned
  fit: true,
  padding: 40,
};
```

### ELK Configuration (large workflows)

```typescript
const ELK_DEFAULTS: ElkLayoutOptions = {
  name: 'elk',
  elk: {
    algorithm: 'layered',
    'elk.direction': 'DOWN',
    'elk.layered.spacing.nodeNodeBetweenLayers': '80',
    'elk.layered.spacing.edgeNodeBetweenLayers': '40',
    'elk.spacing.nodeNode': '60',
    'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
    'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
    // Compound node support
    'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
    // Port-based edge routing
    'elk.layered.considerModelOrder.strategy': 'PREFER_EDGES',
  },
  // Run in web worker to avoid blocking main thread
  workerUrl: '/elk-worker.js',
};
```

### Incremental Layout Strategy

Full relayout on every state change is unacceptable. Strategy:

1. **Pin completed nodes**: Once a node reaches terminal state, its position is locked. Only new/running nodes participate in layout.
2. **Debounce**: Batch state changes within 100ms window before triggering relayout.
3. **Animate transitions**: When relayout computes new positions, animate nodes to their new positions (300ms ease-in-out) rather than jumping.
4. **Delta detection**: Compare new positions to current; if max displacement < 5px, skip the animation entirely.

```typescript
class IncrementalLayoutManager {
  private pinned = new Set<string>();
  private debounceTimer: number | null = null;
  private readonly DEBOUNCE_MS = 100;

  onNodeStateChange(nodeId: string, newStatus: NodeStatus) {
    if (isTerminal(newStatus)) {
      this.pinned.add(nodeId);
    }
    this.scheduleRelayout();
  }

  private scheduleRelayout() {
    if (this.debounceTimer) clearTimeout(this.debounceTimer);
    this.debounceTimer = setTimeout(() => this.relayout(), this.DEBOUNCE_MS);
  }

  private relayout() {
    const layout = this.cy.layout({
      ...DAGRE_DEFAULTS,
      // Lock pinned nodes in place
      boundingBox: undefined,
      animate: true,
      animationDuration: 300,
      // Custom: don't move pinned nodes
      transform: (node, pos) => {
        if (this.pinned.has(node.id())) {
          return node.position(); // keep current position
        }
        return pos;
      },
    });
    layout.run();
  }
}
```

---

## Critical Path Computation

### Algorithm: CPM (Critical Path Method)

Classic two-pass algorithm adapted for real-time updates:

**Forward pass** (earliest start/finish):
```
ES[start] = 0
For each node in topological order:
  ES[node] = max(EF[predecessor] for each predecessor)
  EF[node] = ES[node] + duration(node)
```

**Backward pass** (latest start/finish):
```
LF[end] = EF[end]  // or max(EF) across all sinks
For each node in reverse topological order:
  LF[node] = min(LS[successor] for each successor)
  LS[node] = LF[node] - duration(node)
```

**Slack** = LS[node] - ES[node]. Nodes with slack = 0 are on the critical path.

### Duration Estimation with Mixed Known/Unknown

```typescript
function getEffectiveDuration(node: WorkflowNode): number {
  switch (node.status) {
    case 'completed':
      return node.actualDurationMs!;              // known exactly
    case 'running':
      return Math.max(
        node.elapsedMs,                           // at least this long
        node.estimatedDurationMs ?? node.elapsedMs * 1.5 // estimate or 50% buffer
      );
    case 'failed':
      return node.actualDurationMs! + (node.retryable ? getEffectiveDuration(node) : 0);
    default:
      // pending/ready: use estimate or historical average
      return node.estimatedDurationMs
        ?? node.historicalAvgMs
        ?? DEFAULT_NODE_DURATION_MS;              // 60_000ms fallback
  }
}
```

### Real-time Updates

Recompute is triggered when:
1. A node completes (actual duration replaces estimate)
2. A node starts (clock starts ticking)
3. A running node exceeds its estimate (duration increases)
4. A node fails (may add retry time or remove from path)

**Optimization**: Don't recompute the entire DAG. Only affected subgraph needs update:
- When node N completes: recompute forward pass from N's successors, then backward pass from sinks back to N.
- If slack values change → critical path may have shifted → re-derive path.

```typescript
class CriticalPathEngine {
  private es = new Map<string, number>(); // earliest start
  private ef = new Map<string, number>(); // earliest finish
  private ls = new Map<string, number>(); // latest start
  private lf = new Map<string, number>(); // latest finish
  private topoOrder: string[] = [];

  /** Full recompute — O(V+E), called on workflow start and structural changes */
  computeFull(nodes: WorkflowNode[], edges: WorkflowEdge[]): CriticalPathHighlight {
    this.topoOrder = topologicalSort(nodes, edges);
    // Forward pass
    for (const id of this.topoOrder) {
      const preds = getIncomingNonOptional(id, edges);
      const es = preds.length === 0 ? 0 : Math.max(...preds.map(p => this.ef.get(p)!));
      this.es.set(id, es);
      this.ef.set(id, es + getEffectiveDuration(getNode(id)));
    }
    // Backward pass
    const maxEf = Math.max(...this.topoOrder.map(id => this.ef.get(id)!));
    for (const id of [...this.topoOrder].reverse()) {
      const succs = getOutgoingNonOptional(id, edges);
      const lf = succs.length === 0 ? maxEf : Math.min(...succs.map(s => this.ls.get(s)!));
      this.lf.set(id, lf);
      this.ls.set(id, lf - getEffectiveDuration(getNode(id)));
    }
    return this.derivePath(edges);
  }

  /** Incremental update — O(affected subgraph) */
  updateFrom(changedNodeId: string, edges: WorkflowEdge[]): CriticalPathHighlight {
    // Forward: recompute from changedNodeId through all successors
    const affected = getTransitiveSuccessors(changedNodeId, edges);
    for (const id of this.topoOrder.filter(n => affected.has(n) || n === changedNodeId)) {
      const preds = getIncomingNonOptional(id, edges);
      const es = preds.length === 0 ? 0 : Math.max(...preds.map(p => this.ef.get(p)!));
      this.es.set(id, es);
      this.ef.set(id, es + getEffectiveDuration(getNode(id)));
    }
    // Backward: recompute from sinks back
    const maxEf = Math.max(...this.topoOrder.map(id => this.ef.get(id)!));
    for (const id of [...this.topoOrder].reverse()) {
      const succs = getOutgoingNonOptional(id, edges);
      const lf = succs.length === 0 ? maxEf : Math.min(...succs.map(s => this.ls.get(s)!));
      this.lf.set(id, lf);
      this.ls.set(id, lf - getEffectiveDuration(getNode(id)));
    }
    return this.derivePath(edges);
  }

  private derivePath(edges: WorkflowEdge[]): CriticalPathHighlight {
    const pathNodeIds: string[] = [];
    const slackByNodeId = new Map<string, SlackInfo>();

    for (const id of this.topoOrder) {
      const slack = this.ls.get(id)! - this.es.get(id)!;
      if (Math.abs(slack) < 1) { // floating point tolerance
        pathNodeIds.push(id);
      } else {
        slackByNodeId.set(id, {
          totalSlackMs: slack,
          freeSlackMs: this.computeFreeSlack(id, edges),
          earliestStartMs: this.es.get(id)!,
          latestStartMs: this.ls.get(id)!,
        });
      }
    }
    // Derive path edges (edges where both endpoints are on critical path)
    const pathSet = new Set(pathNodeIds);
    const pathEdgeIds = edges
      .filter(e => pathSet.has(e.sourceId) && pathSet.has(e.targetId) && e.type !== 'optional')
      .map(e => e.id);

    return {
      pathNodeIds,
      pathEdgeIds,
      totalDurationMs: Math.max(...this.topoOrder.map(id => this.ef.get(id)!)),
      elapsedMs: /* computed from workflow startedAt */ 0,
      remainingMs: /* totalDuration - elapsed */ 0,
      slackByNodeId,
      visual: CRITICAL_PATH_VISUAL_DEFAULTS,
      computedAt: Date.now(),
      pathChanged: false, // set by comparing to previous
    };
  }
}
```

### Handling Edge Cases

- **Optional edges**: Excluded from critical path computation entirely. They don't contribute to ES/LF calculations.
- **Cycles**: Detected during topological sort. If found, mark workflow as invalid and show error overlay. (Workflows should be validated at definition time.)
- **Multiple critical paths** (equal-length parallels): All paths with slack=0 are highlighted. The union of all zero-slack nodes forms the critical "band".
- **Running nodes**: Duration increases in real-time (elapsed keeps growing). Recompute every 5s for running nodes to update ETA.

---

## Animation System

### Cytoscape.js Animation API

Cytoscape provides two animation mechanisms:
1. `ele.animate({ style, position, duration, easing })` — per-element
2. `cy.animate({ zoom, pan, duration })` — viewport
3. CSS-based: `transition-property` in stylesheet for hover/select

### State Transition Animations

| Transition | Animation | Duration | Impl |
|-----------|-----------|----------|------|
| → ready | Scale pulse (1.0→1.05→1.0) + border color fade | 400ms | `ele.animate()` |
| → running | Border becomes animated dash + progress overlay appears | continuous | CSS `@keyframes` + canvas overlay |
| → completed | Green flash (bg→green→final) + scale pop (1.0→1.08→1.0) | 500ms | `ele.animate()` chained |
| → failed | Horizontal shake (±4px, 3 cycles) + red border flash | 600ms | `ele.animate()` with custom easing |
| → cancelled | Fade opacity to 0.5 + strikethrough line | 300ms | `ele.animate()` |
| → skipped | Fade opacity to 0.4 + grey overlay | 200ms | `ele.animate()` |
| Edge active | Dash offset animation (marching ants) | continuous | CSS `line-dash-offset` animation |
| Critical path | Pulsing glow (opacity 0.4→1.0→0.4) | 2000ms loop | Canvas overlay layer |

### Animation Budget & Performance

```typescript
class AnimationScheduler {
  private readonly MAX_CONCURRENT = 8;  // max simultaneous animations
  private active = new Map<string, Animation>();
  private queue: AnimationEntry[] = [];

  schedule(entry: AnimationEntry) {
    // Respect prefers-reduced-motion
    if (this.reducedMotion) {
      // Skip animation, apply final state immediately
      this.applyFinalState(entry);
      return;
    }
    if (this.active.size < this.MAX_CONCURRENT) {
      this.play(entry);
    } else {
      // Priority queue — higher priority animations can preempt
      this.queue.push(entry);
      this.queue.sort((a, b) => b.priority - a.priority);
    }
  }

  private play(entry: AnimationEntry) {
    const ele = this.cy.getElementById(entry.targetId);
    if (!ele.length) return;

    const anim = ele.animate(
      this.getAnimationProps(entry),
      { duration: entry.durationMs, easing: 'ease-in-out-sine', complete: () => {
        this.active.delete(entry.targetId);
        this.dequeue();
        entry.onComplete?.();
      }}
    );
    this.active.set(entry.targetId, anim);
  }
}
```

### Progress Ring (Running Nodes)

Progress rings can't be done purely in Cytoscape's style system. Use a **canvas overlay layer**:

```typescript
class ProgressOverlayRenderer {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;

  constructor(private cy: cytoscape.Core) {
    // Create a canvas layer on top of the Cytoscape container
    this.canvas = document.createElement('canvas');
    this.canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;';
    cy.container()!.appendChild(this.canvas);
    this.ctx = this.canvas.getContext('2d')!;
  }

  render(runningNodes: Map<string, NodeProgress>) {
    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    const pan = this.cy.pan();
    const zoom = this.cy.zoom();

    for (const [nodeId, progress] of runningNodes) {
      const node = this.cy.getElementById(nodeId);
      if (!node.length) continue;
      const pos = node.renderedPosition();
      const w = node.renderedWidth();
      const h = node.renderedHeight();

      // Draw progress arc around node
      const cx = pos.x;
      const cy_pos = pos.y;
      const radius = Math.max(w, h) / 2 + 4;
      const startAngle = -Math.PI / 2;
      const endAngle = startAngle + (2 * Math.PI * progress.percent);

      this.ctx.beginPath();
      this.ctx.arc(cx, cy_pos, radius, startAngle, endAngle);
      this.ctx.strokeStyle = progress.overdue ? '#EF4444' : '#F97316';
      this.ctx.lineWidth = 3;
      this.ctx.lineCap = 'round';
      this.ctx.stroke();

      // ETA badge
      if (progress.etaMs !== null) {
        this.drawBadge(cx + w/2 - 10, cy_pos - h/2 - 10, formatEta(progress.etaMs));
      }
    }
    requestAnimationFrame(() => this.render(runningNodes));
  }
}
```

### Reduced Motion Support

```typescript
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// When reduced motion is preferred:
// - Skip all decorative animations (pulse, glow, flow)
// - Keep functional animations (layout transitions) but reduce to 100ms
// - Use instant color changes instead of fades
// - Disable progress ring animation, show static arc
```

---

## Handling Large Workflows (100+ nodes)

### 1. Viewport Culling (Virtualization)

Cytoscape.js renders ALL elements regardless of visibility. For 100+ nodes, use:

```typescript
class ViewportCuller {
  private visibleElements = new Set<string>();
  private readonly BUFFER = 100; // px buffer beyond viewport

  constructor(private cy: cytoscape.Core) {
    // Recompute on viewport change (debounced)
    cy.on('viewport', debounce(() => this.cull(), 50));
  }

  cull() {
    const ext = this.cy.extent();
    const buffered = {
      x1: ext.x1 - this.BUFFER,
      y1: ext.y1 - this.BUFFER,
      x2: ext.x2 + this.BUFFER,
      y2: ext.y2 + this.BUFFER,
    };

    this.cy.elements().forEach(ele => {
      if (ele.isNode()) {
        const pos = ele.position();
        const visible = pos.x >= buffered.x1 && pos.x <= buffered.x2
                     && pos.y >= buffered.y1 && pos.y <= buffered.y2;
        ele.style('display', visible ? 'element' : 'none');
      }
    });
    // Show edges only if both endpoints visible
    this.cy.edges().forEach(edge => {
      const srcVisible = edge.source().style('display') === 'element';
      const tgtVisible = edge.target().style('display') === 'element';
      edge.style('display', (srcVisible || tgtVisible) ? 'element' : 'none');
    });
  }
}
```

### 2. Level-of-Detail (Semantic Zoom)

| Zoom Level | Detail |
|-----------|--------|
| < 0.3 (overview) | Nodes as colored dots, no labels, no badges |
| 0.3 – 0.7 | Nodes with label, status color, no badges |
| 0.7 – 1.5 | Full node rendering (badges, progress, ETA) |
| > 1.5 (detail) | Expanded node with internal info, full edge labels |

```typescript
function applyLevelOfDetail(cy: cytoscape.Core) {
  const zoom = cy.zoom();

  if (zoom < 0.3) {
    cy.style()
      .selector('node').style({ 'label': '', 'width': 20, 'height': 20 })
      .selector('edge').style({ 'width': 1, 'label': '' })
      .update();
  } else if (zoom < 0.7) {
    cy.style()
      .selector('node').style({ 'label': 'data(label)', 'width': 'data(width)', 'height': 'data(height)' })
      .selector('edge').style({ 'width': 2, 'label': '' })
      .update();
  } else {
    // Full detail — styles from NodeVisualState
    cy.style()
      .selector('node').style({ /* full styles */ })
      .selector('edge').style({ /* full styles with labels */ })
      .update();
  }
}
```

### 3. Collapsed Clusters

For sub-workflows or parallel branches with many nodes:

```typescript
class ClusterManager {
  collapseGroup(groupId: string) {
    const children = this.cy.getElementById(groupId).children();
    const internalEdges = children.edgesWith(children);

    // Hide children and internal edges
    children.style('display', 'none');
    internalEdges.style('display', 'none');

    // Show summary node in place of group
    const summary = this.createSummaryNode(groupId, children);
    this.cy.add(summary);

    // Reroute external edges to summary node
    this.rerouteEdges(children, summary.data.id);
  }

  private createSummaryNode(groupId: string, children: cytoscape.Collection) {
    const stateCounts = countStates(children);
    return {
      data: {
        id: `${groupId}:summary`,
        label: `${children.length} tasks`,
        stateCounts,
        // Mini progress bar data
        completedPercent: stateCounts.completed / children.length * 100,
      },
      position: centroid(children),
    };
  }
}
```

### 4. Batch Updates

Never update Cytoscape elements one-by-one in a loop:

```typescript
class BatchUpdater {
  private pending: Array<() => void> = [];
  private scheduled = false;

  queueUpdate(fn: () => void) {
    this.pending.push(fn);
    if (!this.scheduled) {
      this.scheduled = true;
      requestAnimationFrame(() => this.flush());
    }
  }

  private flush() {
    this.cy.startBatch();
    for (const fn of this.pending) fn();
    this.cy.endBatch();
    this.pending = [];
    this.scheduled = false;
  }
}
```

### 5. Memory Budget

| Elements | Approach |
|---------|----------|
| ≤50 | Full render, all animations |
| 50–200 | Dagre layout, reduced animations (max 4 concurrent) |
| 200–500 | ELK worker, viewport culling, LOD, collapsed clusters |
| 500+ | Mandatory clustering, only expanded cluster rendered at once |

---

## Zoom Level Transitions

### Detection

```typescript
function deriveZoomLevel(cy: cytoscape.Core, config: ViewportState): ZoomLevel {
  const zoom = cy.zoom();
  if (zoom < config.thresholds.overviewToWorkflow) return 'overview';
  if (zoom > config.thresholds.workflowToDetail) return 'node-detail';
  return 'workflow';
}
```

### Animated Transitions

```typescript
function drillIntoNode(cy: cytoscape.Core, nodeId: string) {
  const node = cy.getElementById(nodeId);
  cy.animate({
    fit: { eles: node, padding: 80 },
    duration: 500,
    easing: 'ease-in-out-sine',
    complete: () => {
      // After zoom completes, switch to detail rendering
      renderNodeDetail(nodeId);
    }
  });
}

function zoomToOverview(cy: cytoscape.Core) {
  cy.animate({
    fit: { eles: cy.elements(), padding: 40 },
    duration: 400,
    easing: 'ease-out-sine',
  });
}
```

---

## Real-time Update Pipeline

```
WebSocket event
  → WorkflowEventReducer (pure function: event + state → new state)
  → DiffEngine (compute changed nodes/edges)
  → BatchUpdater (queue Cytoscape mutations in rAF)
  → AnimationScheduler (queue transition animations)
  → CriticalPathEngine (incremental recompute if durations changed)
  → ProgressOverlayRenderer (update canvas layer)
```

All updates flow through this pipeline. The 60fps budget means:
- State reduction: <1ms (pure map operations)
- Cytoscape batch: <5ms (startBatch/endBatch)
- Canvas overlay: <2ms (only running nodes)
- Critical path: <5ms incremental (only on node completion)
- Layout: async (debounced, runs in next idle frame)

Total per-frame budget: ~13ms, well within 16.6ms for 60fps.
