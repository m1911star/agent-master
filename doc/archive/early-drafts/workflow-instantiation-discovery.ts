/**
 * Witness Workflow System — Template Instantiation & Agent Discovery
 *
 * This file documents how templates become running workflows and how
 * agents discover, select, and interact with templates.
 */

// ═══════════════════════════════════════════════════════════════════════════════
// TEMPLATE INSTANTIATION PIPELINE
// ═══════════════════════════════════════════════════════════════════════════════
//
// Template → Running Workflow in 6 phases:
//
//   1. RESOLVE    — Fetch template, apply inheritance chain, merge includes
//   2. VALIDATE   — Check parameters, detect cycles, verify slot compatibility
//   3. BIND       — Substitute params into node configs, evaluate conditions
//   4. OPTIMIZE   — Remove unreachable optional nodes, compute critical path
//   5. ACTIVATE   — Create WorkflowInstance, register with engine, start triggers
//   6. EXECUTE    — Engine runs nodes per DAG order, fills slots, tracks progress
//
// Each phase can fail, returning the instantiation to the caller with diagnostics.

import type {
  WorkflowTemplate,
  TemplateParameter,
  TemplateSlot,
  TemplateNode,
  TemplateEdge,
  TemplateInclude,
  TemplateOverride,
  WorkflowTrigger,
  TriggerCondition,
  FiredTrigger,
  SlotFilling,
  InstantiationRequest,
  InstantiationResult,
  InstantiationState,
  ValidationResult,
  AgentContext,
  ScoredTemplate,
  NodeProgress,
} from './workflow-templates-triggers';

import type { AgentRun, AgentEvent, RunStatus } from './data-model';


// ═══════════════════════════════════════════════════════════════════════════════
// Phase 1: RESOLVE — Inheritance & Composition
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Resolution produces a fully-merged template with no unresolved references.
 *
 * Inheritance resolution order (like CSS/class inheritance):
 * 1. Start with the base template (furthest ancestor)
 * 2. Layer each child's additions on top
 * 3. Apply overrides in declaration order
 *
 * Composition (includes) resolution:
 * 1. Resolve each included template recursively
 * 2. Namespace included nodes with the `as` alias: "include_alias.node_id"
 * 3. Merge into the parent's node/edge lists
 * 4. Bind included template's params from parent's paramBindings
 */
export interface ResolvedTemplate {
  /** The fully-merged template (no extends/includes remaining) */
  template: WorkflowTemplate;
  /** Lineage: [root_ancestor, ..., this_template] */
  inheritanceChain: string[];
  /** All included templates (recursively resolved) */
  resolvedIncludes: Map<string, ResolvedTemplate>;
  /** Map from original node IDs to their resolved (possibly namespaced) IDs */
  nodeIdMap: Map<string, string>;
}


// ═══════════════════════════════════════════════════════════════════════════════
// Phase 2: VALIDATE
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Validation checks:
 * - All required parameters have values (from request.params or defaults)
 * - Parameter values satisfy their constraints
 * - DAG is acyclic (edges don't form loops, except explicit retry edges)
 * - All edge endpoints reference existing nodes
 * - All slot references point to defined slots
 * - Included template IDs exist in registry
 * - No conflicting node IDs after resolution
 * - Timeout values are parseable durations
 */
export interface InstantiationValidator {
  validate(
    resolved: ResolvedTemplate,
    params: Record<string, unknown>,
  ): ValidationResult;
}


// ═══════════════════════════════════════════════════════════════════════════════
// Phase 3: BIND — Parameter Substitution
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Expression language for parameter references in templates:
 *
 *   $params.environment        → parameter value
 *   $nodes.build.outputs.path  → output of a completed node
 *   $workflow.status            → current workflow state
 *   $workflow.attemptCount      → retry count for current node
 *   $workflow.startedAt         → workflow start timestamp
 *   $trigger.event             → the event that triggered instantiation
 *   $env.HOME                  → environment variable
 *
 * Binding is lazy for node outputs (resolved at execution time).
 * Binding is eager for parameters and conditions (resolved at instantiation).
 */
export interface BoundTemplate {
  /** Resolved + validated + parameter-bound template */
  template: ResolvedTemplate;
  /** All parameter values (user-provided + defaults) */
  boundParams: Record<string, unknown>;
  /** Nodes included in this instance (optional nodes with false conditions removed) */
  activeNodes: TemplateNode[];
  /** Active edges (edges to removed optional nodes pruned) */
  activeEdges: TemplateEdge[];
  /** Computed execution order (topological sort) */
  executionOrder: string[];
  /** Critical path (longest path through DAG) */
  criticalPath: string[];
}


// ═══════════════════════════════════════════════════════════════════════════════
// Phase 4-6: WORKFLOW INSTANCE (the running entity)
// ═══════════════════════════════════════════════════════════════════════════════

export interface WorkflowInstance {
  /** Unique instance ID */
  id: string;
  /** Template this was instantiated from */
  templateId: string;
  templateVersion: string;
  /** Current lifecycle state */
  state: InstantiationState;
  /** Bound parameters */
  params: Record<string, unknown>;

  /** Per-node execution state */
  nodeStates: Map<string, NodeState>;
  /** Slots and their fill status */
  slotStates: Map<string, SlotState>;

  /** When instantiated */
  createdAt: number;
  /** When execution started */
  startedAt: number | null;
  /** When completed/failed */
  endedAt: number | null;

  /** What initiated this workflow */
  initiator: InstantiationRequest['initiator'];
  /** Parent workflow if sub-workflow */
  parentWorkflowId: string | null;
  /** Child workflows spawned by SubworkflowNodes */
  childWorkflowIds: string[];

  /** Retry tracking */
  attemptCount: number;
  /** Linked agent runs (agents working on this workflow's nodes) */
  agentRunIds: string[];
}

export interface NodeState {
  nodeId: string;
  status: 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped' | 'waiting_slot';
  /** Agent run handling this node (if any) */
  agentRunId: string | null;
  /** Outputs produced */
  outputs: Record<string, unknown>;
  /** When this node started/ended */
  startedAt: number | null;
  endedAt: number | null;
  /** Error info if failed */
  error: string | null;
  /** Retry count for this specific node */
  attempts: number;
}

export interface SlotState {
  slotId: string;
  status: 'pending' | 'claimed' | 'filled' | 'active' | 'completed' | 'timed_out';
  /** Agent that claimed this slot */
  claimedBy: string | null;
  /** The filling (nodes provided by agent) */
  filling: SlotFilling | null;
  /** When the slot was claimed/filled */
  claimedAt: number | null;
  filledAt: number | null;
}


// ═══════════════════════════════════════════════════════════════════════════════
// WORKFLOW ENGINE — Execution Logic
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * The engine drives workflow execution by:
 * 1. Monitoring node readiness (all incoming edges satisfied)
 * 2. Dispatching ready nodes to agents or executing actions directly
 * 3. Collecting outputs and propagating along edges
 * 4. Managing slot filling lifecycle
 * 5. Handling retries, timeouts, and failures
 * 6. Emitting events for real-time visualization
 */
export interface WorkflowEngine {
  /** Instantiate and start a workflow */
  start(request: InstantiationRequest): Promise<InstantiationResult>;

  /** Pause a running workflow */
  suspend(workflowId: string, reason: string): void;

  /** Resume a suspended workflow */
  resume(workflowId: string): void;

  /** Cancel a workflow */
  cancel(workflowId: string, reason: string): void;

  /** Report node completion from an agent */
  completeNode(workflowId: string, nodeId: string, outputs: Record<string, unknown>): void;

  /** Report node failure */
  failNode(workflowId: string, nodeId: string, error: string): void;

  /** Get current state */
  getWorkflow(workflowId: string): WorkflowInstance | null;

  /** List active workflows */
  listActive(): WorkflowInstance[];

  /** Subscribe to workflow events */
  subscribe(handler: (event: WorkflowEngineEvent) => void): () => void;
}

export type WorkflowEngineEvent =
  | { type: 'workflow_created'; workflowId: string; templateId: string }
  | { type: 'workflow_started'; workflowId: string }
  | { type: 'workflow_completed'; workflowId: string; durationMs: number }
  | { type: 'workflow_failed'; workflowId: string; error: string }
  | { type: 'node_ready'; workflowId: string; nodeId: string }
  | { type: 'node_started'; workflowId: string; nodeId: string; agentRunId?: string }
  | { type: 'node_completed'; workflowId: string; nodeId: string; outputs: Record<string, unknown> }
  | { type: 'node_failed'; workflowId: string; nodeId: string; error: string }
  | { type: 'slot_claimed'; workflowId: string; slotId: string; agentRunId: string }
  | { type: 'slot_filled'; workflowId: string; slotId: string }
  | { type: 'slot_timeout'; workflowId: string; slotId: string }
  | { type: 'trigger_fired'; triggerId: string; workflowId: string };


// ═══════════════════════════════════════════════════════════════════════════════
// AGENT DISCOVERY — How agents find templates
// ═══════════════════════════════════════════════════════════════════════════════
//
// Discovery flow:
//
//   1. Agent starts working → Witness observes its events
//   2. Pattern matcher compares agent behavior to known templates
//   3. If match confidence > threshold, Witness suggests the template
//   4. Agent can:
//      a) Accept → workflow is instantiated, agent's work is tracked against it
//      b) Ignore → agent works freestyle, Witness still monitors
//      c) Adapt → agent instantiates template with modifications
//
// Agents can also proactively query:
//   - "What templates match my current task?"
//   - "What slots need filling in active workflows?"
//   - "What's the recommended workflow for deploying?"

/**
 * Pattern matching: detect when an agent is implicitly following a template.
 *
 * The system watches for sequences of tool calls and outputs that match
 * template node patterns. Uses a sliding window approach:
 */
export interface PatternMatcher {
  /** Register a template's expected behavior pattern */
  registerPattern(templateId: string, pattern: BehaviorPattern): void;

  /** Feed an agent event and check for matches */
  processEvent(event: AgentEvent): PatternMatch[];

  /** Get current partial matches (workflows an agent might be following) */
  getPartialMatches(agentRunId: string): PartialPatternMatch[];
}

export interface BehaviorPattern {
  templateId: string;
  /** Sequence of expected behaviors (order matters but gaps allowed) */
  steps: BehaviorStep[];
  /** Minimum confidence to suggest */
  threshold: number;
}

export interface BehaviorStep {
  /** What to look for */
  matcher:
    | { type: 'tool_call'; toolName: string; inputPattern?: string }
    | { type: 'file_change'; pathPattern: string }
    | { type: 'output_contains'; pattern: string }
    | { type: 'sequence'; steps: BehaviorStep[] };
  /** Which template node this maps to */
  mapsToNode: string;
  /** Whether this step is required for a match */
  required: boolean;
}

export interface PatternMatch {
  templateId: string;
  agentRunId: string;
  confidence: number;
  matchedNodes: string[];
  suggestedAction: 'instantiate' | 'track' | 'suggest';
}

export interface PartialPatternMatch {
  templateId: string;
  progress: number;  // 0-1, how much of the pattern has been observed
  matchedSteps: number;
  totalSteps: number;
  lastMatchAt: number;
}


// ═══════════════════════════════════════════════════════════════════════════════
// TEMPLATE INSTANTIATION EXAMPLE (Concrete Flow)
// ═══════════════════════════════════════════════════════════════════════════════
//
// Example: Instantiating the "deploy" template when a git tag is pushed
//
// 1. EVENT: Git tag "v2.1.0" pushed on branch "main"
//
// 2. TRIGGER EVALUATION:
//    - TriggerEvaluator receives: { kind: 'git_event', event: 'tag_created', branch: 'main' }
//    - Matches trigger "deploy-on-tag" (branchPattern "v*" matches "v2.1.0")
//    - Conditions pass: no active deploy workflow running
//    - Extracts params: { branch: 'main', version: 'v2.1.0' }
//
// 3. INSTANTIATION REQUEST:
//    {
//      templateId: 'witness.deploy.standard',
//      params: { environment: 'staging', branch: 'main' },  // defaults + extracted
//      trigger: { triggerId: 'deploy-on-tag', event: {...}, extractedParams: {...} },
//      initiator: { kind: 'trigger', triggerId: 'deploy-on-tag' }
//    }
//
// 4. RESOLVE: Template has no extends/includes → pass-through
//
// 5. VALIDATE:
//    - environment: 'staging' ✓ (valid enum value)
//    - branch: 'main' ✓ (matches pattern)
//    - skipTests: false (default) ✓
//    - DAG: acyclic ✓
//    - Slots: verification_logic defined ✓
//
// 6. BIND:
//    - Optional node "test": condition "$params.skipTests !== true" → true → INCLUDE
//    - All $params.* references substituted
//    - Node outputs remain lazy (resolved at execution)
//
// 7. OPTIMIZE:
//    - All nodes active (skipTests is false)
//    - Critical path: checkout → test → build → approval_gate → deploy → verify → notify
//    - Estimated duration: 30m
//
// 8. ACTIVATE:
//    - WorkflowInstance created with id "wf_abc123"
//    - Node states initialized (all 'pending' except checkout → 'ready')
//    - Slot "verification_logic" state: 'pending'
//    - Engine starts executing
//
// 9. EXECUTE:
//    a) checkout runs (shell command) → completes → outputs { commitSha, commitMessage }
//    b) test becomes ready → agent dispatched → runs tests → outputs { testsPassed, ... }
//    c) build becomes ready → shell command → outputs { artifactPath, artifactSize }
//    d) approval_gate: auto-approves (environment === 'staging')
//    e) deploy: agent dispatched with bound prompt
//    f) verify: slot 'verification_logic' needs filling
//       - Engine broadcasts: "slot available: verification_logic in wf_abc123"
//       - Agent claims slot → provides 2 nodes (health_check, smoke_test)
//       - Slot filled → sub-nodes execute
//    g) notify: sends message with all resolved outputs
//    h) Workflow completes → state: 'completed'
//
// 10. POST-EXECUTION:
//     - Metrics emitted (duration, node timings)
//     - Template stats updated (instantiationCount++, averageDuration recalc)
//     - Hook onComplete fires → notify channel


// ═══════════════════════════════════════════════════════════════════════════════
// AGENT-TEMPLATE INTERACTION PROTOCOL
// ═══════════════════════════════════════════════════════════════════════════════
//
// Agents interact with the template system through these mechanisms:
//
// A. PASSIVE (Witness observes, agent doesn't know):
//    - Pattern matcher watches agent events
//    - If pattern matches template with >80% confidence:
//      → Workflow is tracked against template (visualization shows progress)
//      → No disruption to agent's work
//
// B. SUGGESTED (Witness proposes, agent can accept):
//    - Pattern match at 50-80% confidence, or agent asks for help
//    - Witness sends suggestion via AgentEvent or dashboard notification
//    - Agent can accept (workflow instantiated) or ignore
//
// C. EXPLICIT (Agent requests):
//    - Agent queries: "What templates fit my task?"
//    - Agent instantiates: "Start deploy workflow with params {...}"
//    - Agent fills slot: "I'll handle the verification slot"
//    - Agent reports: "Node X is complete with outputs {...}"
//
// The API surface for agents (exposed via tool or MCP):
//
//   witness.templates.search({ tags: ['deploy'], context: currentTask })
//   witness.templates.instantiate('witness.deploy.standard', { environment: 'prod' })
//   witness.workflows.claimSlot('wf_abc123', 'verification_logic')
//   witness.workflows.reportProgress('wf_abc123', 'deploy', { status: 'completed', outputs: {...} })
//   witness.workflows.declarePattern('witness.debug.fix', { confidence: 0.9, completedNodes: [...] })
