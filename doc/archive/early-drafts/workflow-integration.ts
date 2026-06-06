/**
 * Workflow ↔ Witness Data Model Integration
 * ==========================================
 * How the workflow system layers on top of the existing AgentRun/TopologyEdge/AgentEvent model.
 */

// ─── Re-exports of existing types (for context) ───────────────────────────────
import type { AgentRun, RunStatus, SpawnKind, TopologyEdge, DashboardState, AgentSource } from './data-model';

// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 1: WORKFLOW DETECTION FROM TOPOLOGY DATA
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * A detected workflow candidate before user confirmation or binding.
 * Produced by the detection algorithm scanning TopologyEdge DAGs.
 */
export interface WorkflowCandidate {
  /** Unique ID for this detection (deterministic hash of constituent run IDs) */
  id: string;
  /** The root run that orchestrates this workflow */
  orchestratorRunId: string;
  /** All runs that participate in this workflow */
  participantRunIds: string[];
  /** Detected pattern type */
  pattern: DetectedPattern;
  /** Confidence 0-1 that this is genuinely a workflow, not incidental subagents */
  confidence: number;
  /** When the detection was made */
  detectedAt: number;
  /** Human-readable label inferred from run titles/spawnReasons */
  inferredLabel: string | null;
}

export type DetectedPattern =
  | 'fan-out-gather'     // One parent spawns N parallel children, waits for all
  | 'sequential-chain'   // A → B → C linear handoff/continuation
  | 'dag'                // General directed acyclic graph with mixed parallel/sequential
  | 'scatter-reduce'     // Fan-out with a final aggregation step
  | 'pipeline';          // Sequential stages, each potentially with parallelism within

/**
 * Heuristic signals used to score detection confidence.
 */
export interface DetectionSignals {
  /** Number of direct children from orchestrator */
  fanOutCount: number;
  /** Whether children were spawned within a tight time window (< 5s apart) */
  burstSpawn: boolean;
  /** Whether spawnReasons suggest coordination ("phase-1", "step:lint", task descriptions) */
  coordinatedNaming: boolean;
  /** Whether there are sequential dependencies (child B started after child A ended) */
  hasSequentialDeps: boolean;
  /** Max depth of the subgraph */
  depth: number;
  /** Whether the orchestrator run has 'task' or 'spawn' tool calls interleaved with waits */
  orchestratorPattern: boolean;
}

// ─── Detection Algorithm ──────────────────────────────────────────────────────

/**
 * Main detection entry point. Called whenever topology changes.
 * Scans for workflow-like structures in the run graph.
 */
export function detectWorkflows(
  runs: Map<string, AgentRun>,
  topology: TopologyEdge[]
): WorkflowCandidate[] {
  // Step 1: Build adjacency from topology
  const childrenOf = buildAdjacency(topology);

  // Step 2: Find candidate orchestrators (runs with 2+ subagent children)
  const candidates: WorkflowCandidate[] = [];

  for (const [parentId, edges] of childrenOf) {
    const subagentEdges = edges.filter(e => e.kind === 'subagent' || e.kind === 'fork');
    if (subagentEdges.length < 2) continue; // Single subagent = not a workflow

    const parent = runs.get(parentId);
    if (!parent) continue;

    // Step 3: Gather the full subgraph rooted at this orchestrator
    const participants = gatherSubgraph(parentId, childrenOf);
    const participantRuns = participants
      .map(id => runs.get(id))
      .filter((r): r is AgentRun => r !== undefined);

    // Step 4: Compute detection signals
    const signals = computeSignals(parent, participantRuns, subagentEdges, childrenOf);

    // Step 5: Score confidence
    const confidence = scoreConfidence(signals);
    if (confidence < 0.4) continue; // Below threshold, skip

    // Step 6: Classify pattern
    const pattern = classifyPattern(signals, subagentEdges, childrenOf, parentId);

    candidates.push({
      id: deterministicId(parentId, participants),
      orchestratorRunId: parentId,
      participantRunIds: participants,
      pattern,
      confidence,
      detectedAt: Date.now(),
      inferredLabel: inferLabel(parent, participantRuns),
    });
  }

  // Step 7: Deduplicate (a sub-workflow shouldn't be reported if its parent is also detected)
  return deduplicateNested(candidates);
}

function buildAdjacency(topology: TopologyEdge[]): Map<string, TopologyEdge[]> {
  const map = new Map<string, TopologyEdge[]>();
  for (const edge of topology) {
    const existing = map.get(edge.parentRunId);
    if (existing) {
      existing.push(edge);
    } else {
      map.set(edge.parentRunId, [edge]);
    }
  }
  return map;
}

function gatherSubgraph(rootId: string, childrenOf: Map<string, TopologyEdge[]>): string[] {
  const visited = new Set<string>();
  const queue = [rootId];
  while (queue.length > 0) {
    const current = queue.pop()!;
    if (visited.has(current)) continue;
    visited.add(current);
    const children = childrenOf.get(current);
    if (children) {
      for (const edge of children) {
        queue.push(edge.childRunId);
      }
    }
  }
  // Exclude the root itself from participants (it's the orchestrator)
  visited.delete(rootId);
  return Array.from(visited);
}

function computeSignals(
  orchestrator: AgentRun,
  participants: AgentRun[],
  directEdges: TopologyEdge[],
  childrenOf: Map<string, TopologyEdge[]>
): DetectionSignals {
  const spawnTimes = directEdges.map(e => e.spawnedAt).sort((a, b) => a - b);
  const maxGap = spawnTimes.length > 1
    ? Math.max(...spawnTimes.slice(1).map((t, i) => t - spawnTimes[i]))
    : 0;

  // Burst spawn: all children spawned within 5 seconds
  const burstSpawn = spawnTimes.length > 1 &&
    (spawnTimes[spawnTimes.length - 1] - spawnTimes[0]) < 5000;

  // Coordinated naming: spawnReasons share a pattern
  const reasons = participants.map(r => r.spawnReason).filter(Boolean) as string[];
  const coordinatedNaming = hasCoordinatedNames(reasons);

  // Sequential deps: any child started after another child ended
  const endTimes = participants
    .filter(r => r.endedAt !== null)
    .map(r => r.endedAt!);
  const startTimes = participants.map(r => r.startedAt);
  const hasSequentialDeps = startTimes.some(start =>
    endTimes.some(end => start > end && start - end < 2000)
  );

  // Depth: max nesting level in subgraph
  const depth = computeMaxDepth(orchestrator.id, childrenOf);

  return {
    fanOutCount: directEdges.length,
    burstSpawn,
    coordinatedNaming,
    hasSequentialDeps,
    depth,
    orchestratorPattern: orchestrator.metrics.toolCallsByName['Task'] > 1 ||
      orchestrator.metrics.toolCallsByName['task'] > 1 ||
      orchestrator.metrics.toolCallsByName['delegate_task'] > 0,
  };
}

function scoreConfidence(signals: DetectionSignals): number {
  let score = 0;

  // Fan-out is the primary signal
  if (signals.fanOutCount >= 5) score += 0.35;
  else if (signals.fanOutCount >= 3) score += 0.25;
  else if (signals.fanOutCount >= 2) score += 0.15;

  // Burst spawn suggests intentional parallel dispatch
  if (signals.burstSpawn) score += 0.2;

  // Coordinated naming strongly suggests a designed workflow
  if (signals.coordinatedNaming) score += 0.25;

  // Sequential dependencies suggest orchestration
  if (signals.hasSequentialDeps) score += 0.1;

  // Orchestrator used Task/spawn tools multiple times
  if (signals.orchestratorPattern) score += 0.15;

  // Depth > 1 suggests hierarchical workflow
  if (signals.depth > 1) score += 0.05;

  return Math.min(1, score);
}

function classifyPattern(
  signals: DetectionSignals,
  directEdges: TopologyEdge[],
  childrenOf: Map<string, TopologyEdge[]>,
  orchestratorId: string
): DetectedPattern {
  // Pure fan-out: all children spawned in burst, no sequential deps
  if (signals.burstSpawn && !signals.hasSequentialDeps) {
    return 'fan-out-gather';
  }
  // Sequential chain: depth >= fanout (linear)
  if (signals.depth >= signals.fanOutCount && !signals.burstSpawn) {
    return 'sequential-chain';
  }
  // Pipeline: sequential stages each with potential sub-parallelism
  if (signals.hasSequentialDeps && signals.fanOutCount > 2) {
    return 'pipeline';
  }
  // Default: general DAG
  return 'dag';
}

// ─── Helper utilities (signatures only, implementation straightforward) ───────

declare function hasCoordinatedNames(reasons: string[]): boolean;
declare function computeMaxDepth(rootId: string, childrenOf: Map<string, TopologyEdge[]>): number;
declare function deterministicId(orchestratorId: string, participantIds: string[]): string;
declare function inferLabel(orchestrator: AgentRun, participants: AgentRun[]): string | null;
declare function deduplicateNested(candidates: WorkflowCandidate[]): WorkflowCandidate[];


// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 2: BINDING USER-DEFINED WORKFLOWS TO LIVE RUNS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * User-defined workflow template. Specifies expected structure.
 */
export interface WorkflowTemplate {
  id: string;
  name: string;
  description: string;
  /** Version for template evolution */
  version: number;

  /** The nodes (steps) in this workflow */
  nodes: WorkflowNodeTemplate[];
  /** Dependencies between nodes (edges in the template DAG) */
  edges: WorkflowEdgeTemplate[];

  /** Trigger conditions for auto-binding */
  triggers: WorkflowTrigger[];
}

export interface WorkflowNodeTemplate {
  /** Template-local node ID */
  nodeId: string;
  /** Human label */
  label: string;
  /** Expected characteristics for matching */
  matcher: NodeMatcher;
  /** Whether this node is required or optional */
  required: boolean;
  /** Relative weight for progress calculation (default 1) */
  weight: number;
  /** Expected duration hint in ms (for critical path estimation before actual data) */
  expectedDurationMs: number | null;
}

export interface WorkflowEdgeTemplate {
  from: string;  // nodeId
  to: string;    // nodeId
  /** Whether this is a hard dependency (to cannot start until from completes) */
  kind: 'hard-dependency' | 'soft-dependency' | 'data-flow';
}

/**
 * How to match a template node to an actual AgentRun.
 * Multiple matchers compose with AND logic.
 */
export interface NodeMatcher {
  /** Match on spawnReason containing this substring */
  spawnReasonContains?: string;
  /** Match on run title containing this substring */
  titleContains?: string;
  /** Match on the specific SpawnKind */
  spawnKind?: SpawnKind;
  /** Match on ordinal position among siblings (0-indexed) */
  ordinalPosition?: number;
  /** Match on agent source */
  source?: AgentSource;
  /** Custom predicate key (registered in matcher registry) */
  customMatcherKey?: string;
}

/**
 * Triggers that auto-bind a template when conditions are met.
 */
export type WorkflowTrigger =
  | { kind: 'on-root-spawn'; matchOrchestratorTitle: string }
  | { kind: 'on-fan-out'; minChildren: number; spawnReasonPattern: string }
  | { kind: 'on-detection'; minConfidence: number; patternFilter?: DetectedPattern }
  | { kind: 'manual' }; // User explicitly starts binding

// ─── Node Binding ─────────────────────────────────────────────────────────────

export type BindingStatus =
  | 'unbound'     // No run matched yet
  | 'bound'       // Matched to a specific run
  | 'skipped'     // Run completed without this node executing
  | 'extra';      // Run appeared that doesn't match any template node

export interface NodeBinding {
  /** Template node ID */
  nodeId: string;
  /** Binding state */
  status: BindingStatus;
  /** The matched AgentRun ID (null if unbound/skipped) */
  boundRunId: string | null;
  /** Confidence of this specific binding (0-1) */
  matchConfidence: number;
  /** When the binding was established */
  boundAt: number | null;
  /** If status is 'extra', the run that appeared unexpectedly */
  extraRunId?: string;
}

/**
 * A live workflow instance = template + bindings + state.
 */
export interface WorkflowInstance {
  id: string;
  templateId: string;
  /** The orchestrator run this workflow is bound to */
  orchestratorRunId: string;
  /** Current bindings for each template node */
  bindings: Map<string, NodeBinding>;  // nodeId → NodeBinding
  /** Extra runs detected that don't match any template node */
  extraRuns: ExtraRunRecord[];
  /** Overall binding quality (how well reality matches template) */
  bindingFidelity: number; // 0-1
  /** Current workflow status (derived) */
  status: WorkflowStatus;
  /** Timestamps */
  startedAt: number;
  endedAt: number | null;
}

export interface ExtraRunRecord {
  runId: string;
  detectedAt: number;
  /** Best-guess of where in the DAG this belongs */
  inferredPosition: 'before' | 'after' | 'parallel-to';
  nearestNodeId: string | null;
}

// ─── Binding Algorithm ────────────────────────────────────────────────────────

/**
 * Attempts to bind a newly spawned AgentRun to a workflow instance.
 * Called whenever a new run_spawned_child event is received for a workflow's orchestrator.
 */
export function bindRunToWorkflow(
  instance: WorkflowInstance,
  template: WorkflowTemplate,
  newRun: AgentRun,
  existingRuns: Map<string, AgentRun>
): NodeBinding | ExtraRunRecord {
  // Score each unbound template node against this run
  const unboundNodes = template.nodes.filter(node => {
    const binding = instance.bindings.get(node.nodeId);
    return binding !== undefined && binding.status === 'unbound';
  });

  if (unboundNodes.length === 0) {
    // All nodes already bound - this is an extra run
    return {
      runId: newRun.id,
      detectedAt: Date.now(),
      inferredPosition: inferPosition(newRun, instance, template, existingRuns),
      nearestNodeId: findNearestNode(newRun, instance, template, existingRuns),
    };
  }

  // Score each candidate node
  const scored = unboundNodes.map(node => ({
    node,
    score: computeMatchScore(node.matcher, newRun, instance, existingRuns),
  }));

  // Sort by score descending
  scored.sort((a, b) => b.score - a.score);
  const best = scored[0];

  // Threshold: below 0.3 is likely not a match
  if (best.score < 0.3) {
    return {
      runId: newRun.id,
      detectedAt: Date.now(),
      inferredPosition: inferPosition(newRun, instance, template, existingRuns),
      nearestNodeId: findNearestNode(newRun, instance, template, existingRuns),
    };
  }

  // Bind it
  const binding: NodeBinding = {
    nodeId: best.node.nodeId,
    status: 'bound',
    boundRunId: newRun.id,
    matchConfidence: best.score,
    boundAt: Date.now(),
  };

  return binding;
}

function computeMatchScore(
  matcher: NodeMatcher,
  run: AgentRun,
  instance: WorkflowInstance,
  existingRuns: Map<string, AgentRun>
): number {
  let score = 0;
  let criteria = 0;

  if (matcher.spawnReasonContains) {
    criteria++;
    if (run.spawnReason?.includes(matcher.spawnReasonContains)) {
      score += 1;
    }
  }

  if (matcher.titleContains) {
    criteria++;
    if (run.title?.toLowerCase().includes(matcher.titleContains.toLowerCase())) {
      score += 1;
    }
  }

  if (matcher.spawnKind) {
    criteria++;
    if (run.spawnKind === matcher.spawnKind) {
      score += 1;
    }
  }

  if (matcher.source) {
    criteria++;
    if (run.source === matcher.source) {
      score += 1;
    }
  }

  if (matcher.ordinalPosition !== undefined) {
    criteria++;
    // Count how many siblings are already bound (this run's ordinal among siblings)
    const siblingsBound = Array.from(instance.bindings.values())
      .filter(b => b.status === 'bound')
      .length;
    if (siblingsBound === matcher.ordinalPosition) {
      score += 0.8; // Slightly less confident on ordinal
    }
  }

  if (criteria === 0) return 0.5; // No matchers defined, neutral score
  return score / criteria;
}

/**
 * Handle workflow completion: mark unmatched required nodes as skipped.
 */
export function finalizeWorkflow(
  instance: WorkflowInstance,
  template: WorkflowTemplate
): void {
  for (const node of template.nodes) {
    const binding = instance.bindings.get(node.nodeId);
    if (binding && binding.status === 'unbound') {
      binding.status = 'skipped';
    }
  }
  // Recalculate fidelity
  instance.bindingFidelity = computeBindingFidelity(instance, template);
}

function computeBindingFidelity(instance: WorkflowInstance, template: WorkflowTemplate): number {
  const requiredNodes = template.nodes.filter(n => n.required);
  if (requiredNodes.length === 0) return 1;

  const boundRequired = requiredNodes.filter(n => {
    const b = instance.bindings.get(n.nodeId);
    return b !== undefined && b.status === 'bound';
  });

  const extraPenalty = instance.extraRuns.length * 0.05; // Small penalty per extra run
  return Math.max(0, (boundRequired.length / requiredNodes.length) - extraPenalty);
}

declare function inferPosition(
  run: AgentRun,
  instance: WorkflowInstance,
  template: WorkflowTemplate,
  existingRuns: Map<string, AgentRun>
): 'before' | 'after' | 'parallel-to';

declare function findNearestNode(
  run: AgentRun,
  instance: WorkflowInstance,
  template: WorkflowTemplate,
  existingRuns: Map<string, AgentRun>
): string | null;


// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 3: NEW EVENTS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Base event fields (from existing model).
 */
interface BaseEvent {
  eventId: string;
  runId: string;
  turnId: string | null;
  timestamp: number;
  source: AgentSource;
  monotonicSeq: number;
}

/**
 * Workflow lifecycle transitions.
 *
 * `runId` = the orchestrator run that owns this workflow.
 * This keeps workflow events queryable by run (existing index).
 */
export interface WorkflowLifecycleEvent extends BaseEvent {
  kind: 'workflow_detected' | 'workflow_created' | 'workflow_started'
    | 'workflow_completed' | 'workflow_failed' | 'workflow_cancelled';
  data: {
    workflowId: string;
    templateId: string | null;       // null for auto-detected workflows
    pattern?: DetectedPattern;       // for detected workflows
    confidence?: number;             // for detected workflows
    endReason?: string;              // for completed/failed/cancelled
    fidelity?: number;               // how well reality matched template (at end)
  };
}

/**
 * Emitted when a workflow node is matched to an AgentRun.
 *
 * Trigger: bindRunToWorkflow() succeeds with score >= threshold.
 */
export interface NodeBindingEvent extends BaseEvent {
  kind: 'node_bound' | 'node_skipped' | 'node_extra_detected';
  data: {
    workflowId: string;
    nodeId: string;
    boundRunId: string | null;       // The AgentRun matched (null for skipped)
    matchConfidence: number;
    templateNodeLabel: string;
  };
}

/**
 * Emitted periodically or on state change to communicate progress.
 *
 * Trigger: Any constituent run changes status (run_ended, metric_delta).
 * Throttled to max 1 per second per workflow.
 */
export interface WorkflowProgressEvent extends BaseEvent {
  kind: 'workflow_progress';
  data: {
    workflowId: string;
    progress: number;                // 0-1 overall
    criticalPathMs: number;          // Current critical path estimate
    bottleneckNodeId: string | null; // Currently slowest node on critical path
    nodeStatuses: Record<string, WorkflowNodeStatus>;
  };
}

export type WorkflowNodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

/**
 * Extended AgentEvent union including workflow events.
 */
export type ExtendedAgentEvent =
  | WorkflowLifecycleEvent
  | NodeBindingEvent
  | WorkflowProgressEvent;
  // Original: RunLifecycleEvent | TurnEvent | StepEvent | MetricEvent

/**
 * Event emission triggers:
 *
 * workflow_detected    → detectWorkflows() finds new candidate with confidence >= 0.6
 * workflow_created     → User manually creates a workflow from template
 * workflow_started     → First node transitions to 'running' (first child run_started)
 * workflow_completed   → All required nodes are 'completed' or 'skipped'
 * workflow_failed      → Any required node is 'failed' and not retried
 * workflow_cancelled   → Orchestrator run is cancelled
 *
 * node_bound          → bindRunToWorkflow() returns a NodeBinding
 * node_skipped        → finalizeWorkflow() marks unmatched required nodes
 * node_extra_detected → bindRunToWorkflow() returns ExtraRunRecord
 *
 * workflow_progress   → On run status change for any participant, throttled 1/sec
 */


// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 4: STATE DERIVATION
// ═══════════════════════════════════════════════════════════════════════════════

export type WorkflowStatus =
  | 'detected'    // Auto-detected, not yet confirmed by user
  | 'pending'     // Created/bound but no nodes running yet
  | 'running'     // At least one node is active
  | 'completed'   // All required nodes done successfully
  | 'failed'      // A required node failed irrecoverably
  | 'cancelled'   // User or orchestrator cancelled
  | 'stale';      // No activity for extended period

/**
 * State machine transitions:
 *
 * detected  → pending     (user confirms / template auto-binds)
 * detected  → cancelled   (user dismisses detection)
 * pending   → running     (first node starts)
 * pending   → cancelled   (orchestrator cancelled before start)
 * running   → completed   (all required nodes done)
 * running   → failed      (required node failed, no retry)
 * running   → cancelled   (orchestrator cancelled)
 * running   → stale       (no activity for > staleThresholdMs)
 * stale     → running     (activity resumes)
 * stale     → failed      (timeout exceeded)
 *
 * Terminal states: completed, failed, cancelled
 * Non-terminal: detected, pending, running, stale
 */

/**
 * Complete derived state for a workflow instance.
 */
export interface WorkflowState {
  workflowId: string;
  templateId: string | null;
  status: WorkflowStatus;

  /** Progress metrics */
  progress: WorkflowProgress;

  /** Per-node derived state */
  nodes: Map<string, DerivedNodeState>;

  /** Critical path analysis */
  criticalPath: CriticalPathResult;

  /** Aggregate metrics across all participant runs */
  aggregateMetrics: AggregateWorkflowMetrics;

  /** Timestamps */
  startedAt: number | null;
  endedAt: number | null;
  lastActivityAt: number;
}

export interface DerivedNodeState {
  nodeId: string;
  label: string;
  status: WorkflowNodeStatus;
  boundRunId: string | null;
  /** Derived from the bound run's status */
  runStatus: RunStatus | null;
  /** Duration so far or total */
  durationMs: number;
  /** Progress within this node (0-1), based on run metrics if available */
  nodeProgress: number;
  /** Weight from template (default 1) */
  weight: number;
}

export interface WorkflowProgress {
  /** Weighted completion ratio: sum(completedWeight) / sum(totalWeight) */
  completionRatio: number;
  /** Time-based: elapsed / estimated total duration */
  timeRatio: number;
  /** Combined (prefer completion for accuracy, time for liveness) */
  combined: number;
  /** Counts */
  nodesTotal: number;
  nodesCompleted: number;
  nodesRunning: number;
  nodesFailed: number;
  nodesPending: number;
}

export interface CriticalPathResult {
  /** Ordered list of node IDs on the critical path */
  path: string[];
  /** Total estimated duration of the critical path in ms */
  totalMs: number;
  /** Currently active node on the critical path (the bottleneck) */
  currentBottleneck: string | null;
  /** Estimated time remaining (based on critical path) */
  estimatedRemainingMs: number;
}

export interface AggregateWorkflowMetrics {
  totalInputTokens: number;
  totalOutputTokens: number;
  totalEstimatedCostUsd: number;
  totalToolCalls: number;
  totalErrors: number;
  wallClockDurationMs: number;
  /** Sum of all node durations (shows parallelism gain) */
  cumulativeNodeDurationMs: number;
  /** Parallelism factor: cumulative / wallClock */
  parallelismFactor: number;
}

// ─── State Derivation Algorithm ───────────────────────────────────────────────

/**
 * Derives the complete WorkflowState from constituent AgentRun states.
 * Pure function: no side effects, fully deterministic.
 */
export function deriveWorkflowState(
  instance: WorkflowInstance,
  template: WorkflowTemplate | null,
  runs: Map<string, AgentRun>
): WorkflowState {
  const nodes = deriveNodeStates(instance, template, runs);
  const status = deriveWorkflowStatus(nodes, instance);
  const progress = computeProgress(nodes, template);
  const criticalPath = computeCriticalPath(nodes, template);
  const aggregateMetrics = computeAggregateMetrics(instance, runs);

  const participantRuns = Array.from(instance.bindings.values())
    .filter(b => b.boundRunId !== null)
    .map(b => runs.get(b.boundRunId!))
    .filter((r): r is AgentRun => r !== undefined);

  const lastActivityAt = participantRuns.length > 0
    ? Math.max(...participantRuns.map(r => r.lastEventAt))
    : instance.startedAt;

  return {
    workflowId: instance.id,
    templateId: instance.templateId,
    status,
    progress,
    nodes,
    criticalPath,
    aggregateMetrics,
    startedAt: instance.startedAt,
    endedAt: instance.endedAt,
    lastActivityAt,
  };
}

function deriveNodeStates(
  instance: WorkflowInstance,
  template: WorkflowTemplate | null,
  runs: Map<string, AgentRun>
): Map<string, DerivedNodeState> {
  const result = new Map<string, DerivedNodeState>();

  for (const [nodeId, binding] of instance.bindings) {
    const templateNode = template?.nodes.find(n => n.nodeId === nodeId);
    const boundRun = binding.boundRunId ? runs.get(binding.boundRunId) : null;

    const status = deriveNodeStatus(binding, boundRun);
    const durationMs = boundRun
      ? (boundRun.endedAt ?? Date.now()) - boundRun.startedAt
      : 0;

    result.set(nodeId, {
      nodeId,
      label: templateNode?.label ?? nodeId,
      status,
      boundRunId: binding.boundRunId,
      runStatus: boundRun?.status ?? null,
      durationMs,
      nodeProgress: computeNodeProgress(status, boundRun),
      weight: templateNode?.weight ?? 1,
    });
  }

  return result;
}

function deriveNodeStatus(binding: NodeBinding, run: AgentRun | null): WorkflowNodeStatus {
  if (binding.status === 'skipped') return 'skipped';
  if (binding.status === 'unbound') return 'pending';
  if (!run) return 'pending';

  switch (run.status) {
    case 'live':
    case 'idle':
      return 'running';
    case 'completed':
      return 'completed';
    case 'failed':
      return 'failed';
    case 'cancelled':
      return 'failed'; // Treat cancelled nodes as failed for workflow purposes
    case 'stale':
    case 'unknown':
      return 'running'; // Optimistic: might resume
  }
}

function computeNodeProgress(status: WorkflowNodeStatus, run: AgentRun | null): number {
  switch (status) {
    case 'completed': return 1;
    case 'failed': return 0; // Failed nodes don't contribute
    case 'skipped': return 1; // Skipped counts as "done" for progress
    case 'pending': return 0;
    case 'running': {
      if (!run) return 0;
      // Heuristic: use tool call count as rough progress indicator
      // In practice, could be replaced with explicit progress reporting
      // For now, use time-based sigmoid (fast start, slow finish)
      const elapsed = Date.now() - run.startedAt;
      const expectedMs = 60000; // 1 min default expectation
      return Math.min(0.95, elapsed / (elapsed + expectedMs)); // Asymptotic approach to 1
    }
  }
}

/**
 * Derive overall workflow status from node statuses.
 */
function deriveWorkflowStatus(
  nodes: Map<string, DerivedNodeState>,
  instance: WorkflowInstance
): WorkflowStatus {
  if (instance.status === 'detected') return 'detected';

  const statuses = Array.from(nodes.values()).map(n => n.status);

  // Any required node failed → workflow failed
  // (In real impl, check template.nodes[].required)
  if (statuses.some(s => s === 'failed')) return 'failed';

  // All done (completed or skipped) → workflow completed
  if (statuses.every(s => s === 'completed' || s === 'skipped')) return 'completed';

  // Any running → workflow running
  if (statuses.some(s => s === 'running')) return 'running';

  // All pending → pending
  if (statuses.every(s => s === 'pending')) return 'pending';

  return 'running'; // Mixed pending + completed = still running
}

// ─── Progress Calculation ─────────────────────────────────────────────────────

function computeProgress(
  nodes: Map<string, DerivedNodeState>,
  template: WorkflowTemplate | null
): WorkflowProgress {
  let totalWeight = 0;
  let completedWeight = 0;
  let nodesCompleted = 0;
  let nodesRunning = 0;
  let nodesFailed = 0;
  let nodesPending = 0;

  for (const node of nodes.values()) {
    totalWeight += node.weight;

    switch (node.status) {
      case 'completed':
      case 'skipped':
        completedWeight += node.weight;
        nodesCompleted++;
        break;
      case 'running':
        // Partial credit for running nodes
        completedWeight += node.weight * node.nodeProgress;
        nodesRunning++;
        break;
      case 'failed':
        nodesFailed++;
        break;
      case 'pending':
        nodesPending++;
        break;
    }
  }

  const completionRatio = totalWeight > 0 ? completedWeight / totalWeight : 0;

  // Time ratio requires knowing total expected duration (from critical path)
  // Placeholder: use completion ratio as proxy
  const timeRatio = completionRatio;

  return {
    completionRatio,
    timeRatio,
    combined: completionRatio * 0.7 + timeRatio * 0.3, // Prefer completion accuracy
    nodesTotal: nodes.size,
    nodesCompleted,
    nodesRunning,
    nodesFailed,
    nodesPending,
  };
}

// ─── Critical Path Computation ────────────────────────────────────────────────

/**
 * Computes the critical path through the workflow DAG.
 * Uses longest-path algorithm on the template's dependency graph,
 * with actual durations for completed/running nodes and estimates for pending.
 */
function computeCriticalPath(
  nodes: Map<string, DerivedNodeState>,
  template: WorkflowTemplate | null
): CriticalPathResult {
  if (!template || template.edges.length === 0) {
    // No dependency graph: critical path is just all nodes in parallel
    // Longest single node is the bottleneck
    let maxDuration = 0;
    let bottleneck: string | null = null;
    for (const node of nodes.values()) {
      if (node.durationMs > maxDuration) {
        maxDuration = node.durationMs;
        bottleneck = node.nodeId;
      }
    }
    return {
      path: bottleneck ? [bottleneck] : [],
      totalMs: maxDuration,
      currentBottleneck: bottleneck,
      estimatedRemainingMs: 0,
    };
  }

  // Build DAG adjacency and in-degree for topological sort
  const adj = new Map<string, string[]>();
  const inDegree = new Map<string, number>();

  for (const node of template.nodes) {
    adj.set(node.nodeId, []);
    inDegree.set(node.nodeId, 0);
  }

  for (const edge of template.edges) {
    if (edge.kind === 'hard-dependency') {
      adj.get(edge.from)?.push(edge.to);
      inDegree.set(edge.to, (inDegree.get(edge.to) ?? 0) + 1);
    }
  }

  // Longest path via topological order
  // dist[node] = longest path from any source to this node (inclusive)
  const dist = new Map<string, number>();
  const predecessor = new Map<string, string | null>();

  // Topological sort (Kahn's algorithm)
  const queue: string[] = [];
  for (const [nodeId, deg] of inDegree) {
    if (deg === 0) {
      queue.push(nodeId);
      const nodeState = nodes.get(nodeId);
      const duration = getEffectiveDuration(nodeId, nodeState, template);
      dist.set(nodeId, duration);
      predecessor.set(nodeId, null);
    }
  }

  const topoOrder: string[] = [];
  while (queue.length > 0) {
    const current = queue.shift()!;
    topoOrder.push(current);

    for (const next of (adj.get(current) ?? [])) {
      const newDeg = (inDegree.get(next) ?? 1) - 1;
      inDegree.set(next, newDeg);

      const nextState = nodes.get(next);
      const nextDuration = getEffectiveDuration(next, nextState, template);
      const candidate = (dist.get(current) ?? 0) + nextDuration;

      if (candidate > (dist.get(next) ?? 0)) {
        dist.set(next, candidate);
        predecessor.set(next, current);
      }

      if (newDeg === 0) {
        queue.push(next);
      }
    }
  }

  // Find the node with maximum distance (end of critical path)
  let maxDist = 0;
  let endNode: string | null = null;
  for (const [nodeId, d] of dist) {
    if (d > maxDist) {
      maxDist = d;
      endNode = nodeId;
    }
  }

  // Trace back the critical path
  const path: string[] = [];
  let current: string | null = endNode;
  while (current !== null) {
    path.unshift(current);
    current = predecessor.get(current) ?? null;
  }

  // Find current bottleneck (first 'running' node on critical path)
  const currentBottleneck = path.find(nodeId => {
    const state = nodes.get(nodeId);
    return state?.status === 'running';
  }) ?? null;

  // Estimate remaining: sum of pending/running durations on critical path
  let estimatedRemainingMs = 0;
  for (const nodeId of path) {
    const state = nodes.get(nodeId);
    if (!state) continue;
    if (state.status === 'pending') {
      const templateNode = template.nodes.find(n => n.nodeId === nodeId);
      estimatedRemainingMs += templateNode?.expectedDurationMs ?? 60000;
    } else if (state.status === 'running') {
      const templateNode = template.nodes.find(n => n.nodeId === nodeId);
      const expected = templateNode?.expectedDurationMs ?? 60000;
      const elapsed = state.durationMs;
      estimatedRemainingMs += Math.max(0, expected - elapsed);
    }
  }

  return {
    path,
    totalMs: maxDist,
    currentBottleneck,
    estimatedRemainingMs,
  };
}

function getEffectiveDuration(
  nodeId: string,
  nodeState: DerivedNodeState | undefined,
  template: WorkflowTemplate
): number {
  // Use actual duration if node is completed/running
  if (nodeState && (nodeState.status === 'completed' || nodeState.status === 'running')) {
    return nodeState.durationMs;
  }
  // Otherwise use template's expected duration
  const templateNode = template.nodes.find(n => n.nodeId === nodeId);
  return templateNode?.expectedDurationMs ?? 60000; // Default 1 min
}

function computeAggregateMetrics(
  instance: WorkflowInstance,
  runs: Map<string, AgentRun>
): AggregateWorkflowMetrics {
  let totalInputTokens = 0;
  let totalOutputTokens = 0;
  let totalEstimatedCostUsd = 0;
  let totalToolCalls = 0;
  let totalErrors = 0;
  let minStart = Infinity;
  let maxEnd = 0;
  let cumulativeNodeDurationMs = 0;

  for (const binding of instance.bindings.values()) {
    if (!binding.boundRunId) continue;
    const run = runs.get(binding.boundRunId);
    if (!run) continue;

    totalInputTokens += run.metrics.inputTokens;
    totalOutputTokens += run.metrics.outputTokens;
    totalEstimatedCostUsd += run.metrics.estimatedCostUsd;
    totalToolCalls += run.metrics.toolCallCount;
    totalErrors += run.metrics.errors;

    minStart = Math.min(minStart, run.startedAt);
    const endTime = run.endedAt ?? run.lastEventAt;
    maxEnd = Math.max(maxEnd, endTime);
    cumulativeNodeDurationMs += endTime - run.startedAt;
  }

  const wallClockDurationMs = maxEnd > minStart ? maxEnd - minStart : 0;

  return {
    totalInputTokens,
    totalOutputTokens,
    totalEstimatedCostUsd,
    totalToolCalls,
    totalErrors,
    wallClockDurationMs,
    cumulativeNodeDurationMs,
    parallelismFactor: wallClockDurationMs > 0
      ? cumulativeNodeDurationMs / wallClockDurationMs
      : 1,
  };
}


// ═══════════════════════════════════════════════════════════════════════════════
// SECTION 5: DASHBOARD STATE INTEGRATION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Extended DashboardState with workflow layer.
 * Workflow state is a DERIVED LAYER on top of existing state —
 * it does not replace or modify runs/topology/events.
 */
export interface ExtendedDashboardState extends DashboardState {
  /** Active workflow instances */
  workflows: Map<string, WorkflowState>;
  /** Detected but unconfirmed workflow candidates */
  workflowCandidates: WorkflowCandidate[];
  /** User-defined templates available for binding */
  workflowTemplates: Map<string, WorkflowTemplate>;
}

/**
 * Workflow reducer: integrates workflow events into dashboard state.
 * Sits alongside the existing event reducer.
 */
export function reduceWorkflowEvent(
  state: ExtendedDashboardState,
  event: ExtendedAgentEvent
): ExtendedDashboardState {
  switch (event.kind) {
    case 'workflow_detected': {
      // Add to candidates list
      const candidate: WorkflowCandidate = {
        id: event.data.workflowId,
        orchestratorRunId: event.runId,
        participantRunIds: [], // Populated from topology
        pattern: event.data.pattern ?? 'dag',
        confidence: event.data.confidence ?? 0,
        detectedAt: event.timestamp,
        inferredLabel: null,
      };
      return {
        ...state,
        workflowCandidates: [...state.workflowCandidates, candidate],
      };
    }

    case 'workflow_started':
    case 'workflow_completed':
    case 'workflow_failed':
    case 'workflow_cancelled': {
      // Re-derive state for this workflow
      // In practice: trigger deriveWorkflowState() and update the map
      const wfId = event.data.workflowId;
      const existing = state.workflows.get(wfId);
      if (existing) {
        const updated = { ...existing };
        if (event.kind === 'workflow_completed') updated.status = 'completed';
        if (event.kind === 'workflow_failed') updated.status = 'failed';
        if (event.kind === 'workflow_cancelled') updated.status = 'cancelled';
        const newMap = new Map(state.workflows);
        newMap.set(wfId, updated);
        return { ...state, workflows: newMap };
      }
      return state;
    }

    case 'workflow_progress': {
      const wfId = event.data.workflowId;
      const existing = state.workflows.get(wfId);
      if (existing) {
        const updated: WorkflowState = {
          ...existing,
          progress: { ...existing.progress, completionRatio: event.data.progress },
          criticalPath: {
            ...existing.criticalPath,
            totalMs: event.data.criticalPathMs,
            currentBottleneck: event.data.bottleneckNodeId,
          },
        };
        const newMap = new Map(state.workflows);
        newMap.set(wfId, updated);
        return { ...state, workflows: newMap };
      }
      return state;
    }

    default:
      return state;
  }
}

/**
 * Stale threshold for workflows (5 minutes without activity).
 */
const WORKFLOW_STALE_THRESHOLD_MS = 5 * 60 * 1000;

/**
 * Periodic tick: re-derive workflow states from current run states.
 * Called every time DashboardState.runs changes (after normal event reduction).
 */
export function tickWorkflowStates(state: ExtendedDashboardState): ExtendedDashboardState {
  // 1. Run detection on current topology
  const newCandidates = detectWorkflows(state.runs, state.topology);
  const existingIds = new Set(state.workflowCandidates.map(c => c.id));
  const freshCandidates = newCandidates.filter(c => !existingIds.has(c.id));

  // 2. Check for stale workflows
  const updatedWorkflows = new Map(state.workflows);
  for (const [id, wf] of updatedWorkflows) {
    if (wf.status === 'running' &&
        Date.now() - wf.lastActivityAt > WORKFLOW_STALE_THRESHOLD_MS) {
      updatedWorkflows.set(id, { ...wf, status: 'stale' });
    }
  }

  return {
    ...state,
    workflowCandidates: [...state.workflowCandidates, ...freshCandidates],
    workflows: updatedWorkflows,
  };
}
