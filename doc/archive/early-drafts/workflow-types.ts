/**
 * Witness Workflow System — Type Definitions
 *
 * Integrates with the existing AgentRun/TopologyEdge model to support:
 * 1. User-defined workflows (task sequences with dependencies)
 * 2. Agent-detected workflows (autonomous subagent DAGs)
 * 3. Hybrid workflows (user templates filled by agent runtime)
 *
 * STATE MACHINE TRANSITIONS
 * ═════════════════════════
 *
 * Workflow Lifecycle:
 * ┌───────┐     ┌────────┐     ┌─────────┐     ┌───────────┐
 * │ draft │────▶│ queued │────▶│ running │────▶│ completed │
 * └───────┘     └────────┘     └─────────┘     └───────────┘
 *                                 │  ▲              ▲
 *                                 │  │              │
 *                                 ▼  │              │
 *                              ┌────────┐           │
 *                              │ paused │───────────┘
 *                              └────────┘
 *                                 │
 *                                 ▼
 *                 ┌──────────┐  ┌───────────┐
 *                 │  failed  │  │ cancelled │
 *                 └──────────┘  └───────────┘
 *
 * Valid transitions:
 *   draft     → queued, cancelled
 *   queued    → running, cancelled
 *   running   → paused, completed, failed, cancelled
 *   paused    → running, completed, cancelled
 *   completed → (terminal)
 *   failed    → queued (retry)
 *   cancelled → (terminal)
 *
 * Node Lifecycle:
 * ┌─────────┐     ┌───────┐     ┌─────────┐     ┌───────────┐
 * │ pending │────▶│ ready │────▶│ running │────▶│ completed │
 * └─────────┘     └───────┘     └─────────┘     └───────────┘
 *      │               │              │
 *      ▼               ▼              ▼
 * ┌─────────┐     ┌─────────┐   ┌────────┐
 * │ blocked │     │ skipped │   │ failed │
 * └─────────┘     └─────────┘   └────────┘
 *      │
 *      ▼
 * ┌───────┐
 * │ ready │ (when blocker resolves)
 * └───────┘
 *
 * Valid transitions:
 *   pending   → ready, blocked, skipped
 *   ready     → running, skipped
 *   running   → completed, failed
 *   blocked   → ready, skipped
 *   completed → (terminal)
 *   failed    → ready (retry)
 *   skipped   → (terminal)
 */

import type {
  AgentRun,
  AgentSource,
  RunMetrics,
  RunStatus,
  SpawnKind,
  TopologyEdge,
} from './data-model';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. STATE MACHINE FOUNDATION
// ═══════════════════════════════════════════════════════════════════════════════

/** All possible workflow-level states */
export type WorkflowState =
  | 'draft'
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** All possible node-level states */
export type NodeState =
  | 'pending'
  | 'ready'
  | 'running'
  | 'completed'
  | 'failed'
  | 'blocked'
  | 'skipped';

/**
 * Maps each workflow state to its valid successor states.
 * Used at runtime to validate transitions; at compile time to constrain dispatch.
 */
export type WorkflowTransitions = {
  readonly draft: 'queued' | 'cancelled';
  readonly queued: 'running' | 'cancelled';
  readonly running: 'paused' | 'completed' | 'failed' | 'cancelled';
  readonly paused: 'running' | 'completed' | 'cancelled';
  readonly completed: never;
  readonly failed: 'queued';
  readonly cancelled: never;
};

/**
 * Maps each node state to its valid successor states.
 */
export type NodeTransitions = {
  readonly pending: 'ready' | 'blocked' | 'skipped';
  readonly ready: 'running' | 'skipped';
  readonly running: 'completed' | 'failed';
  readonly blocked: 'ready' | 'skipped';
  readonly completed: never;
  readonly failed: 'ready';
  readonly skipped: never;
};

/**
 * Generic state transition record — captures who/what triggered the transition and when.
 * @template S - The state type (WorkflowState or NodeState)
 */
export interface StateTransition<S extends string> {
  /** State before the transition */
  readonly from: S;
  /** State after the transition */
  readonly to: S;
  /** When the transition occurred (epoch ms) */
  readonly timestamp: number;
  /** What triggered this transition */
  readonly trigger: TransitionTrigger;
  /** Optional human-readable reason */
  readonly reason?: string;
}

/** What caused a state transition */
export type TransitionTrigger =
  | { kind: 'user'; userId: string }
  | { kind: 'system'; rule: string }
  | { kind: 'agent'; runId: string }
  | { kind: 'dependency'; nodeId: string }
  | { kind: 'timeout'; durationMs: number }
  | { kind: 'error'; message: string };

/**
 * Type-safe transition validator.
 * Usage: `canTransition<WorkflowTransitions>('running', 'paused')` → true at type level
 */
export type CanTransition<
  Map extends Record<string, string>,
  From extends keyof Map,
  To extends string,
> = To extends Map[From] ? true : false;

// ═══════════════════════════════════════════════════════════════════════════════
// 2. WORKFLOW ORIGIN — Discriminated Union
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * User explicitly defined this workflow via the UI or API.
 * Has a template, pre-defined nodes, and clear sequencing.
 */
export interface UserDefinedOrigin {
  readonly kind: 'user-defined';
  /** ID of the template this was instantiated from (if any) */
  readonly templateId: string | null;
  /** Version of the template used at instantiation time */
  readonly templateVersion: number | null;
  /** User who created this workflow instance */
  readonly createdBy: string;
  /** Trigger that caused instantiation (manual, schedule, webhook, etc.) */
  readonly trigger: WorkflowTrigger;
}

/**
 * System detected this workflow by observing agent behavior.
 * Constructed retroactively from TopologyEdge + AgentRun data.
 */
export interface AgentDetectedOrigin {
  readonly kind: 'agent-detected';
  /** The root AgentRun that spawned this workflow's DAG */
  readonly rootRunId: string;
  /** Confidence score (0–1) that this is a coherent workflow vs. ad-hoc spawning */
  readonly confidence: number;
  /** The detection method that identified this workflow */
  readonly detectionMethod: DetectionMethod;
  /** When the system first identified this as a workflow */
  readonly detectedAt: number;
  /** TopologyEdges that form the backbone of this detected workflow */
  readonly sourceEdges: readonly TopologyEdge[];
}

/**
 * A user-defined template that agents fill in at runtime.
 * Template provides structure; agents decide specifics (which tools, how many subagents, etc.)
 */
export interface HybridOrigin {
  readonly kind: 'hybrid';
  /** The template providing the skeleton */
  readonly templateId: string;
  /** Version of the template */
  readonly templateVersion: number;
  /** User who set up the template */
  readonly createdBy: string;
  /** The root AgentRun executing this workflow */
  readonly rootRunId: string;
  /** Which nodes came from the template vs. were added by agents at runtime */
  readonly nodeOrigins: ReadonlyMap<string, 'template' | 'agent-added'>;
  /** Merge strategy used to reconcile template expectations with agent behavior */
  readonly mergeStrategy: HybridMergeStrategy;
}

/** Discriminated union of all workflow origins */
export type WorkflowOrigin =
  | UserDefinedOrigin
  | AgentDetectedOrigin
  | HybridOrigin;

/** How a workflow was triggered */
export type WorkflowTrigger =
  | { kind: 'manual'; userId: string }
  | { kind: 'schedule'; cronExpression: string; scheduledAt: number }
  | { kind: 'webhook'; source: string; payload?: unknown }
  | { kind: 'event'; eventType: string; eventId: string }
  | { kind: 'dependency'; upstreamWorkflowId: string };

/** Methods used to detect agent-generated workflows */
export type DetectionMethod =
  | 'topology-dag'          // Inferred from TopologyEdge parent→child relationships
  | 'spawn-pattern'         // Recognized a known spawn pattern (e.g., fan-out/fan-in)
  | 'task-tool-analysis'    // Parsed Task tool calls to reconstruct intent
  | 'temporal-clustering';  // Grouped co-temporal runs under same rootRunId

// ═══════════════════════════════════════════════════════════════════════════════
// 3. WORKFLOW NODE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * A single task/step within the workflow DAG.
 * May or may not be backed by an AgentRun — nodes that haven't started yet have no run.
 */
export interface WorkflowNode {
  /** Unique ID within this workflow */
  readonly id: string;
  /** ID of the parent workflow */
  readonly workflowId: string;
  /** Human-readable label (e.g., "Run unit tests", "Deploy to staging") */
  readonly label: string;
  /** Longer description of what this node does */
  readonly description: string | null;

  // ─── State ────────────────────────────────────────────────────────────────────

  /** Current lifecycle state */
  state: NodeState;
  /** Full history of state transitions */
  readonly stateHistory: readonly StateTransition<NodeState>[];
  /** When this node entered its current state */
  readonly stateEnteredAt: number;

  // ─── Agent Integration ────────────────────────────────────────────────────────

  /** The AgentRun executing this node (null if not yet started or if this is a virtual node) */
  readonly boundRunId: string | null;
  /** Expected agent source for this node (constraint from template) */
  readonly expectedSource: AgentSource | null;
  /** Expected spawn kind when this node starts */
  readonly expectedSpawnKind: SpawnKind;

  // ─── Execution Config ─────────────────────────────────────────────────────────

  /** Maximum time (ms) this node is allowed to run before being marked failed */
  readonly timeoutMs: number | null;
  /** Number of automatic retries on failure (0 = no retry) */
  readonly maxRetries: number;
  /** Current retry count */
  retryCount: number;
  /** Conditions that must be true for this node to transition from pending → ready */
  readonly preconditions: readonly NodePrecondition[];
  /** Priority weight for scheduling (higher = scheduled first when multiple nodes are ready) */
  readonly priority: number;

  // ─── Estimation ───────────────────────────────────────────────────────────────

  /** Estimated duration in ms (from historical data or template) */
  readonly estimatedDurationMs: number | null;
  /** Actual start time (epoch ms) */
  startedAt: number | null;
  /** Actual end time (epoch ms) */
  endedAt: number | null;

  // ─── Metadata ─────────────────────────────────────────────────────────────────

  /** Arbitrary key-value metadata (e.g., git branch, target environment) */
  readonly metadata: Readonly<Record<string, unknown>>;
  /** Whether this node was part of the original template or added at runtime */
  readonly origin: 'template' | 'agent-added' | 'user-added';
  /** Position hint for visualization (layout engine may override) */
  readonly position?: { x: number; y: number };
}

/** Preconditions beyond simple dependency edges */
export type NodePrecondition =
  | { kind: 'approval'; approver: string; approved: boolean }
  | { kind: 'time-window'; after: number; before?: number }
  | { kind: 'external-signal'; signalName: string; received: boolean }
  | { kind: 'expression'; expr: string; satisfied: boolean };

// ═══════════════════════════════════════════════════════════════════════════════
// 4. WORKFLOW EDGE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * A dependency relationship between two nodes in the workflow DAG.
 * Semantically parallel to TopologyEdge but at the workflow abstraction level.
 */
export interface WorkflowEdge {
  /** Unique edge ID */
  readonly id: string;
  /** Source node (must complete/produce output before target can proceed) */
  readonly sourceNodeId: string;
  /** Target node (depends on source) */
  readonly targetNodeId: string;
  /** Type of dependency relationship */
  readonly kind: EdgeKind;
  /** Optional condition — target only proceeds if this evaluates true on source's output */
  readonly condition: EdgeCondition | null;
  /** If this edge was detected from a TopologyEdge, reference it */
  readonly sourceTopologyEdgeId: string | null;
  /** Visual label for the edge (shown on diagram) */
  readonly label: string | null;
}

/** Types of dependency relationships between nodes */
export type EdgeKind =
  | 'completion'       // Target waits for source to complete (finish-to-start)
  | 'data-flow'       // Target needs output data from source
  | 'resource'        // Target needs a resource that source holds/produces
  | 'approval'        // Target needs explicit human approval after source completes
  | 'soft-dependency' // Preferred ordering but not strictly required
  | 'cancellation';   // If source fails/cancels, target is auto-skipped

/** Conditional edge evaluation */
export type EdgeCondition =
  | { kind: 'status'; requiredStatus: 'completed' | 'failed' }
  | { kind: 'output-match'; jsonPath: string; expectedValue: unknown }
  | { kind: 'expression'; expr: string }
  | { kind: 'always' };

// ═══════════════════════════════════════════════════════════════════════════════
// 5. WORKFLOW METRICS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Real-time metrics for a running workflow.
 * Recomputed on every state change; drives the visualization layer.
 */
export interface WorkflowMetrics {
  // ─── Progress ─────────────────────────────────────────────────────────────────

  /** Overall progress as a fraction (0.0–1.0) based on completed node weight */
  readonly progressFraction: number;
  /** Number of nodes in each state */
  readonly nodeStateCounts: Readonly<Record<NodeState, number>>;
  /** Total number of nodes */
  readonly totalNodes: number;

  // ─── Critical Path ────────────────────────────────────────────────────────────

  /** The critical path — longest chain determining minimum completion time */
  readonly criticalPath: CriticalPathInfo;

  // ─── Bottlenecks ──────────────────────────────────────────────────────────────

  /** Nodes that are currently blocking other nodes from starting */
  readonly bottlenecks: readonly BottleneckInfo[];
  /** Nodes currently in 'blocked' state */
  readonly blockedNodes: readonly BlockedNodeInfo[];

  // ─── Time Estimates ───────────────────────────────────────────────────────────

  /** Estimated time to completion (ms from now) — null if cannot estimate */
  readonly estimatedRemainingMs: number | null;
  /** Estimated completion timestamp (epoch ms) */
  readonly estimatedCompletionAt: number | null;
  /** Actual elapsed time since workflow started (ms) */
  readonly elapsedMs: number;

  // ─── Aggregate Cost ───────────────────────────────────────────────────────────

  /** Sum of RunMetrics across all bound AgentRuns in this workflow */
  readonly aggregateMetrics: AggregateWorkflowMetrics;

  // ─── Timestamps ───────────────────────────────────────────────────────────────

  /** When these metrics were last computed */
  readonly computedAt: number;
}

/**
 * Critical path through the workflow DAG.
 * The longest (by estimated duration) path from any pending/running node to completion.
 */
export interface CriticalPathInfo {
  /** Ordered list of node IDs on the critical path */
  readonly nodeIds: readonly string[];
  /** Total estimated duration of the critical path (ms) */
  readonly totalEstimatedMs: number;
  /** How much of the critical path is already completed (ms) */
  readonly completedMs: number;
  /** The current "active" node on the critical path (the one running or next-ready) */
  readonly activeNodeId: string | null;
  /** Slack time — how much delay the non-critical paths can absorb (ms) */
  readonly slackMs: number;
}

/**
 * A node that's blocking downstream progress.
 */
export interface BottleneckInfo {
  /** The node causing the bottleneck */
  readonly nodeId: string;
  /** How many downstream nodes are waiting on this one (direct + transitive) */
  readonly blockedDownstreamCount: number;
  /** How long this node has been in its current state (ms) */
  readonly dwellTimeMs: number;
  /** Whether this node is on the critical path */
  readonly onCriticalPath: boolean;
  /** Severity: ratio of blocked downstream to total remaining */
  readonly severity: number;
}

/** Info about a node that's in 'blocked' state */
export interface BlockedNodeInfo {
  /** The blocked node */
  readonly nodeId: string;
  /** What's blocking it — list of unsatisfied dependency node IDs */
  readonly blockedBy: readonly string[];
  /** Unsatisfied preconditions */
  readonly unsatisfiedPreconditions: readonly NodePrecondition[];
  /** How long it's been blocked (ms) */
  readonly blockedDurationMs: number;
}

/**
 * Aggregated metrics across all agent runs in the workflow.
 * Sum of individual RunMetrics for cost/token tracking.
 */
export interface AggregateWorkflowMetrics {
  /** Total input tokens consumed across all runs */
  readonly totalInputTokens: number;
  /** Total output tokens produced */
  readonly totalOutputTokens: number;
  /** Total estimated cost in USD */
  readonly totalEstimatedCostUsd: number;
  /** Total tool calls made */
  readonly totalToolCalls: number;
  /** Total wall-clock time of all runs (sum, not max — parallel runs add up) */
  readonly totalRunDurationMs: number;
  /** Total errors across all runs */
  readonly totalErrors: number;
  /** Breakdown by agent source */
  readonly bySource: Readonly<Record<string, Partial<RunMetrics>>>;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 6. WORKFLOW VERSION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Tracks template evolution. Each modification to a WorkflowTemplate creates a new version.
 * Instances reference the version they were created from.
 */
export interface WorkflowVersion {
  /** Version number (monotonically increasing within a template) */
  readonly version: number;
  /** ID of the template this version belongs to */
  readonly templateId: string;
  /** When this version was created */
  readonly createdAt: number;
  /** Who created this version */
  readonly createdBy: string;
  /** Human-readable changelog entry */
  readonly changeDescription: string;
  /** Semantic version tag (optional, for user-facing display) */
  readonly semver: string | null;
  /** Diff from previous version — which nodes/edges were added, removed, modified */
  readonly diff: VersionDiff;
  /** Whether this version is the currently active one for new instantiations */
  readonly isActive: boolean;
  /** SHA-256 hash of the serialized template at this version (integrity check) */
  readonly contentHash: string;
}

/** Structured diff between workflow template versions */
export interface VersionDiff {
  /** Node IDs that were added */
  readonly nodesAdded: readonly string[];
  /** Node IDs that were removed */
  readonly nodesRemoved: readonly string[];
  /** Node IDs that were modified (with field-level changes) */
  readonly nodesModified: readonly { nodeId: string; fields: readonly string[] }[];
  /** Edge IDs that were added */
  readonly edgesAdded: readonly string[];
  /** Edge IDs that were removed */
  readonly edgesRemoved: readonly string[];
  /** Changes to workflow-level config */
  readonly configChanges: readonly string[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// 7. WORKFLOW (Main Container)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * A Workflow instance — the primary container representing a coordinated set of tasks.
 * Can be user-defined, agent-detected, or hybrid.
 */
export interface Workflow {
  /** Globally unique workflow ID */
  readonly id: string;
  /** Human-readable name */
  readonly name: string;
  /** Longer description */
  readonly description: string | null;

  // ─── Origin & Identity ────────────────────────────────────────────────────────

  /** How this workflow came to exist */
  readonly origin: WorkflowOrigin;
  /** Tags for filtering/grouping (e.g., "deployment", "refactor", "migration") */
  readonly tags: readonly string[];

  // ─── Lifecycle State ──────────────────────────────────────────────────────────

  /** Current workflow state */
  state: WorkflowState;
  /** Complete history of state transitions */
  readonly stateHistory: readonly StateTransition<WorkflowState>[];
  /** When the workflow was created */
  readonly createdAt: number;
  /** When the workflow started executing (entered 'running') */
  startedAt: number | null;
  /** When the workflow reached a terminal state */
  endedAt: number | null;

  // ─── DAG Structure ────────────────────────────────────────────────────────────

  /** All nodes in this workflow */
  readonly nodes: ReadonlyMap<string, WorkflowNode>;
  /** All edges (dependencies) between nodes */
  readonly edges: readonly WorkflowEdge[];

  // ─── Metrics & Observability ──────────────────────────────────────────────────

  /** Current real-time metrics (recomputed on state changes) */
  metrics: WorkflowMetrics;

  // ─── Configuration ────────────────────────────────────────────────────────────

  /** Maximum concurrent nodes allowed to run simultaneously */
  readonly maxConcurrency: number | null;
  /** Global timeout for the entire workflow (ms) */
  readonly timeoutMs: number | null;
  /** Whether to auto-cancel remaining nodes when any node fails */
  readonly failFast: boolean;
  /** Retry policy at the workflow level */
  readonly retryPolicy: RetryPolicy | null;

  // ─── Integration ──────────────────────────────────────────────────────────────

  /** Root AgentRun ID (if this workflow is tied to an agent execution tree) */
  readonly rootRunId: string | null;
  /** All AgentRun IDs that are part of this workflow */
  readonly boundRunIds: readonly string[];
  /** Workspace context inherited by all nodes */
  readonly workspace: {
    readonly cwd: string | null;
    readonly gitBranch: string | null;
    readonly gitRepo: string | null;
  } | null;
}

/** Retry policy for automatic failure recovery */
export interface RetryPolicy {
  /** Maximum number of retries */
  readonly maxRetries: number;
  /** Backoff strategy */
  readonly backoff: 'none' | 'linear' | 'exponential';
  /** Initial delay between retries (ms) */
  readonly initialDelayMs: number;
  /** Maximum delay between retries (ms) */
  readonly maxDelayMs: number;
  /** Which failure types are retryable */
  readonly retryableErrors: readonly string[] | 'all';
}

// ═══════════════════════════════════════════════════════════════════════════════
// 8. WORKFLOW TEMPLATE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * A reusable workflow template. Instantiated to create Workflow instances.
 * Templates define the DAG skeleton; instances fill in runtime specifics.
 */
export interface WorkflowTemplate {
  /** Unique template ID */
  readonly id: string;
  /** Template name */
  readonly name: string;
  /** Description of what this template does */
  readonly description: string;
  /** Current active version number */
  readonly currentVersion: number;
  /** All versions of this template */
  readonly versions: readonly WorkflowVersion[];
  /** Who created this template */
  readonly createdBy: string;
  /** When it was first created */
  readonly createdAt: number;
  /** When it was last modified */
  readonly updatedAt: number;
  /** Tags for discoverability */
  readonly tags: readonly string[];

  // ─── Template Definition (at current version) ─────────────────────────────────

  /** Node definitions (IDs are stable across versions for diffing) */
  readonly nodeDefinitions: readonly WorkflowNodeTemplate[];
  /** Edge definitions */
  readonly edgeDefinitions: readonly WorkflowEdgeTemplate[];
  /** Default configuration for instances */
  readonly defaults: WorkflowTemplateDefaults;

  // ─── Parameterization ─────────────────────────────────────────────────────────

  /** Parameters that must/can be provided at instantiation time */
  readonly parameters: readonly TemplateParameter[];
  /** Trigger configurations — what can auto-instantiate this template */
  readonly triggers: readonly WorkflowTriggerConfig[];
}

/** A node as defined in a template (not yet instantiated) */
export interface WorkflowNodeTemplate {
  /** Stable ID within the template */
  readonly id: string;
  /** Human-readable label */
  readonly label: string;
  /** Description */
  readonly description: string | null;
  /** Expected agent source */
  readonly expectedSource: AgentSource | null;
  /** Timeout constraint */
  readonly timeoutMs: number | null;
  /** Retry limit */
  readonly maxRetries: number;
  /** Priority weight */
  readonly priority: number;
  /** Preconditions */
  readonly preconditions: readonly NodePrecondition[];
  /** Estimated duration (from historical averages) */
  readonly estimatedDurationMs: number | null;
  /** Whether agents can modify/skip this node at runtime (for hybrid workflows) */
  readonly agentModifiable: boolean;
  /** Metadata template — can include parameter references like {{param_name}} */
  readonly metadata: Readonly<Record<string, unknown>>;
}

/** An edge as defined in a template */
export interface WorkflowEdgeTemplate {
  /** Stable edge ID */
  readonly id: string;
  /** Source node template ID */
  readonly sourceNodeId: string;
  /** Target node template ID */
  readonly targetNodeId: string;
  /** Dependency kind */
  readonly kind: EdgeKind;
  /** Condition */
  readonly condition: EdgeCondition | null;
  /** Label */
  readonly label: string | null;
}

/** Default configuration values for template instantiation */
export interface WorkflowTemplateDefaults {
  readonly maxConcurrency: number | null;
  readonly timeoutMs: number | null;
  readonly failFast: boolean;
  readonly retryPolicy: RetryPolicy | null;
}

/** A parameter that can be injected at instantiation time */
export interface TemplateParameter {
  /** Parameter name (used in {{name}} references in node metadata) */
  readonly name: string;
  /** Human-readable label */
  readonly label: string;
  /** Description */
  readonly description: string | null;
  /** Type constraint */
  readonly type: 'string' | 'number' | 'boolean' | 'select' | 'json';
  /** Whether this must be provided (vs. has a default) */
  readonly required: boolean;
  /** Default value if not provided */
  readonly defaultValue: unknown;
  /** For 'select' type — allowed values */
  readonly options?: readonly { value: unknown; label: string }[];
  /** Validation regex (for string type) */
  readonly validation?: string;
}

/** Configuration for auto-triggering a workflow template */
export interface WorkflowTriggerConfig {
  /** Unique ID for this trigger */
  readonly id: string;
  /** Whether this trigger is currently active */
  readonly enabled: boolean;
  /** Trigger type */
  readonly trigger: WorkflowTrigger;
  /** Parameter values to use when auto-instantiating */
  readonly parameterValues: Readonly<Record<string, unknown>>;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 9. WORKFLOW DETECTION (Agent-Detected Workflows from TopologyEdge)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Result of running the workflow detection algorithm over TopologyEdge data.
 * The detector analyzes a tree/DAG of AgentRuns and identifies coherent workflows.
 *
 * Detection Algorithm (conceptual):
 * 1. Group TopologyEdges by rootRunId
 * 2. For each group, build the DAG (parent → children)
 * 3. Score the DAG on "workflow-likeness" (structured fan-out, sequential chains, etc.)
 * 4. If score > threshold, create a WorkflowDetectionResult
 * 5. Map each AgentRun to a WorkflowNode; each TopologyEdge to a WorkflowEdge
 * 6. Infer node labels from AgentRun titles/spawnReasons
 */
export interface WorkflowDetectionResult {
  /** Whether a coherent workflow was detected */
  readonly detected: boolean;
  /** Confidence score (0–1) */
  readonly confidence: number;
  /** The detection method used */
  readonly method: DetectionMethod;
  /** Root run that anchors the detected workflow */
  readonly rootRunId: string;

  // ─── Detected Structure ───────────────────────────────────────────────────────

  /** Mapped nodes — one per AgentRun in the detected workflow */
  readonly detectedNodes: readonly DetectedNode[];
  /** Mapped edges — derived from TopologyEdges */
  readonly detectedEdges: readonly DetectedEdge[];

  // ─── Pattern Recognition ──────────────────────────────────────────────────────

  /** Recognized structural pattern (if any) */
  readonly pattern: WorkflowPattern | null;
  /** Suggested workflow name (inferred from root run title or spawn reasons) */
  readonly suggestedName: string;
  /** Suggested tags based on detected tool usage patterns */
  readonly suggestedTags: readonly string[];

  // ─── Matching Against Templates ───────────────────────────────────────────────

  /** If this matches a known template, which one and how well */
  readonly templateMatch: TemplateMatchResult | null;
}

/** A node detected from an AgentRun */
export interface DetectedNode {
  /** Generated node ID */
  readonly nodeId: string;
  /** The AgentRun this was derived from */
  readonly runId: string;
  /** Inferred label (from run title, spawn reason, or first tool call) */
  readonly inferredLabel: string;
  /** Run status mapped to a NodeState */
  readonly mappedState: NodeState;
  /** The AgentRun's current status (for reference) */
  readonly runStatus: RunStatus;
  /** How this node was connected in the topology */
  readonly spawnKind: SpawnKind;
}

/** An edge detected from a TopologyEdge */
export interface DetectedEdge {
  /** Generated edge ID */
  readonly edgeId: string;
  /** Source node ID (maps to parent run) */
  readonly sourceNodeId: string;
  /** Target node ID (maps to child run) */
  readonly targetNodeId: string;
  /** The original TopologyEdge */
  readonly sourceTopologyEdge: TopologyEdge;
  /** Inferred edge kind based on spawn semantics */
  readonly inferredKind: EdgeKind;
}

/** Recognized structural patterns in agent DAGs */
export type WorkflowPattern =
  | 'fan-out-fan-in'     // One parent spawns N children, then aggregates
  | 'pipeline'           // Sequential chain A → B → C → D
  | 'map-reduce'         // Parallel processing followed by reduction
  | 'scatter-gather'     // Fan-out with partial result collection
  | 'hierarchical'       // Multi-level delegation tree
  | 'iterative'          // Loop/retry pattern detected
  | 'unknown';           // Structure detected but no known pattern matches

/** Result of matching a detected workflow against known templates */
export interface TemplateMatchResult {
  /** Template that matched */
  readonly templateId: string;
  /** How well the detected structure matches (0–1) */
  readonly similarity: number;
  /** Nodes in the detected workflow that map to template nodes */
  readonly nodeMapping: ReadonlyMap<string, string>; // detectedNodeId → templateNodeId
  /** Nodes in the detection that don't exist in the template */
  readonly unmatchedDetected: readonly string[];
  /** Template nodes that weren't matched by any detected node */
  readonly unmatchedTemplate: readonly string[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// 10. HYBRID MERGE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Strategy for merging a user-defined template with agent runtime decisions.
 * Defines how conflicts between template expectations and agent behavior are resolved.
 */
export type HybridMergeStrategy =
  | 'template-authoritative'  // Template structure wins; agent additions are annotations only
  | 'agent-authoritative'     // Agent can override/skip template nodes freely
  | 'collaborative'           // Conflicts flagged for user review
  | 'adaptive';              // System learns from past merges to auto-resolve

/**
 * Result of merging a template with observed agent behavior.
 * Produced when a hybrid workflow detects that agent execution diverges from the template.
 */
export interface HybridMergeResult {
  /** The workflow ID being merged */
  readonly workflowId: string;
  /** Template used as the base */
  readonly templateId: string;
  /** Template version */
  readonly templateVersion: number;
  /** The merge strategy applied */
  readonly strategy: HybridMergeStrategy;
  /** When the merge was computed */
  readonly mergedAt: number;

  // ─── Merge Outcomes ───────────────────────────────────────────────────────────

  /** Template nodes that were executed as expected */
  readonly matched: readonly MergeMatch[];
  /** Template nodes that the agent skipped or replaced */
  readonly divergences: readonly MergeDivergence[];
  /** Nodes the agent added that weren't in the template */
  readonly additions: readonly MergeAddition[];
  /** Conflicts that need user resolution (only in 'collaborative' strategy) */
  readonly conflicts: readonly MergeConflict[];

  // ─── Summary ──────────────────────────────────────────────────────────────────

  /** Overall alignment score (0–1): how closely the agent followed the template */
  readonly alignmentScore: number;
  /** Whether the merge is considered successful (all conflicts resolved, no critical divergences) */
  readonly success: boolean;
}

/** A template node matched by agent execution */
export interface MergeMatch {
  /** Template node ID */
  readonly templateNodeId: string;
  /** The workflow node that fulfilled it */
  readonly workflowNodeId: string;
  /** The bound AgentRun */
  readonly runId: string;
  /** Match quality (exact match vs. approximate) */
  readonly quality: 'exact' | 'approximate' | 'reinterpreted';
}

/** A template node that the agent diverged from */
export interface MergeDivergence {
  /** Template node ID */
  readonly templateNodeId: string;
  /** What the agent did instead */
  readonly divergenceKind: 'skipped' | 'replaced' | 'reordered' | 'split' | 'merged';
  /** If replaced/split, which workflow nodes took its place */
  readonly replacementNodeIds: readonly string[];
  /** Agent's apparent reason (inferred from spawn reason or tool calls) */
  readonly inferredReason: string | null;
  /** Severity: how much this divergence affects workflow correctness */
  readonly severity: 'info' | 'warning' | 'critical';
}

/** A node the agent added beyond the template */
export interface MergeAddition {
  /** The new workflow node ID */
  readonly workflowNodeId: string;
  /** Where in the DAG it was inserted (after which template node) */
  readonly insertedAfter: string | null;
  /** Why the agent added this (inferred) */
  readonly inferredReason: string | null;
  /** The bound AgentRun */
  readonly runId: string;
}

/** A conflict requiring user resolution */
export interface MergeConflict {
  /** Unique conflict ID */
  readonly id: string;
  /** What's conflicting */
  readonly kind: 'ordering' | 'skip-vs-required' | 'unexpected-failure' | 'resource-contention';
  /** Template's expectation */
  readonly templateExpectation: string;
  /** What actually happened */
  readonly agentBehavior: string;
  /** Suggested resolution options */
  readonly resolutions: readonly ConflictResolution[];
  /** Whether this has been resolved */
  resolved: boolean;
  /** Chosen resolution (after user picks) */
  chosenResolution: ConflictResolution | null;
}

/** A possible resolution for a merge conflict */
export interface ConflictResolution {
  /** Resolution ID */
  readonly id: string;
  /** Human-readable description */
  readonly description: string;
  /** What this resolution does */
  readonly action: 'accept-agent' | 'enforce-template' | 'skip-node' | 'retry-node' | 'custom';
  /** If 'custom', what changes to apply */
  readonly customAction?: unknown;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 11. WORKFLOW EVENTS (Real-Time UI Updates)
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Events emitted by the workflow engine for real-time UI subscription.
 * The frontend subscribes to these to update the workflow visualization.
 */
export type WorkflowEvent =
  | WorkflowStateChangeEvent
  | NodeStateChangeEvent
  | WorkflowMetricsUpdateEvent
  | WorkflowDetectedEvent
  | HybridMergeEvent
  | WorkflowErrorEvent;

interface BaseWorkflowEvent {
  /** Monotonic event ID */
  readonly eventId: string;
  /** Workflow this event belongs to */
  readonly workflowId: string;
  /** When this event occurred */
  readonly timestamp: number;
}

/** Workflow transitioned to a new state */
export interface WorkflowStateChangeEvent extends BaseWorkflowEvent {
  readonly kind: 'workflow-state-change';
  readonly transition: StateTransition<WorkflowState>;
}

/** A node transitioned to a new state */
export interface NodeStateChangeEvent extends BaseWorkflowEvent {
  readonly kind: 'node-state-change';
  readonly nodeId: string;
  readonly transition: StateTransition<NodeState>;
  /** Updated metrics after this change */
  readonly updatedMetrics: WorkflowMetrics;
}

/** Metrics were recomputed (periodic or on significant change) */
export interface WorkflowMetricsUpdateEvent extends BaseWorkflowEvent {
  readonly kind: 'metrics-update';
  readonly metrics: WorkflowMetrics;
  /** What triggered the recomputation */
  readonly trigger: 'node-state-change' | 'periodic' | 'manual';
}

/** A new workflow was detected from agent behavior */
export interface WorkflowDetectedEvent extends BaseWorkflowEvent {
  readonly kind: 'workflow-detected';
  readonly detection: WorkflowDetectionResult;
}

/** A hybrid merge occurred or a conflict needs resolution */
export interface HybridMergeEvent extends BaseWorkflowEvent {
  readonly kind: 'hybrid-merge';
  readonly merge: HybridMergeResult;
  /** Whether user action is needed (conflicts exist) */
  readonly needsResolution: boolean;
}

/** An error occurred within the workflow engine */
export interface WorkflowErrorEvent extends BaseWorkflowEvent {
  readonly kind: 'workflow-error';
  readonly nodeId: string | null;
  readonly error: {
    readonly code: string;
    readonly message: string;
    readonly recoverable: boolean;
  };
}

// ═══════════════════════════════════════════════════════════════════════════════
// 12. UTILITY TYPES
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Maps RunStatus to the appropriate NodeState for detection.
 * Used when converting AgentRun status to workflow node status.
 */
export type RunStatusToNodeState = {
  readonly live: 'running';
  readonly idle: 'running';
  readonly completed: 'completed';
  readonly failed: 'failed';
  readonly cancelled: 'skipped';
  readonly stale: 'blocked';
  readonly unknown: 'pending';
};

/**
 * Type-safe transition dispatch — ensures only valid transitions are dispatched.
 * @example
 * function transitionWorkflow<From extends WorkflowState>(
 *   workflow: Workflow & { state: From },
 *   to: WorkflowTransitions[From],
 *   trigger: TransitionTrigger
 * ): void
 */
export type ValidTransition<
  S extends string,
  Map extends Record<S, string>,
  From extends S,
> = Map[From];

/**
 * Creates a workflow from a detection result.
 * Input: TopologyEdge[] + AgentRun[]
 * Output: Workflow with origin.kind === 'agent-detected'
 */
export interface WorkflowDetectionInput {
  /** All topology edges in the tree being analyzed */
  readonly edges: readonly TopologyEdge[];
  /** All agent runs referenced by those edges */
  readonly runs: ReadonlyMap<string, AgentRun>;
  /** The root run ID to analyze from */
  readonly rootRunId: string;
  /** Minimum confidence threshold (0–1) to consider it a workflow */
  readonly confidenceThreshold: number;
  /** Known templates to match against */
  readonly knownTemplates: readonly WorkflowTemplate[];
}

/**
 * Configuration for the hybrid merge algorithm.
 */
export interface HybridMergeConfig {
  /** The template being used */
  readonly template: WorkflowTemplate;
  /** The version of the template */
  readonly version: number;
  /** Merge strategy */
  readonly strategy: HybridMergeStrategy;
  /** How aggressively to match agent nodes to template nodes */
  readonly matchingStrictness: 'strict' | 'moderate' | 'loose';
  /** Whether to auto-resolve non-critical conflicts */
  readonly autoResolveNonCritical: boolean;
  /** Maximum divergence score (0–1) before flagging the whole workflow as "off-template" */
  readonly maxDivergenceThreshold: number;
}

/**
 * Subscription handle for workflow events.
 * Returned by the workflow engine when a client subscribes.
 */
export interface WorkflowSubscription {
  /** Unique subscription ID */
  readonly id: string;
  /** Which workflow is being observed */
  readonly workflowId: string;
  /** Event filter — only receive these event kinds */
  readonly filter: readonly WorkflowEvent['kind'][] | 'all';
  /** Unsubscribe */
  readonly unsubscribe: () => void;
}
