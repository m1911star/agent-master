/**
 * Witness Workflow System — Runtime Augmentation Protocol
 * ========================================================
 *
 * How agents dynamically add nodes to running workflows.
 *
 * This is the "section 4 companion document" referenced by the DSL schema.
 * Runtime augmentation is the bridge between static YAML definitions and
 * adaptive agent behavior — agents DISCOVER tasks during execution and
 * extend the DAG without human intervention (unless constraints require it).
 *
 * Design principles:
 *   1. DAG invariant preserved — augmentation CANNOT create cycles
 *   2. Bounded expansion — max_children, cost_budget, timeout limits enforced
 *   3. Observable — all augmentations emit events for real-time viz
 *   4. Reversible — augmented nodes can be cancelled without corrupting the DAG
 *   5. Constrained — only nodes marked augmentable:true accept injections
 */

import type { AgentRun, AgentSource, TopologyEdge } from './data-model';

// ═══════════════════════════════════════════════════════════════════════════════
// 1. AUGMENTATION EVENT — How an agent signals "add a node"
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Emitted by an agent (via workflow_augment tool) to request a node insertion.
 * The workflow engine intercepts this from the AgentEvent stream.
 *
 * Lifecycle:
 *   Agent calls workflow_augment tool → engine receives AugmentationRequest →
 *   validates constraints → creates AugmentedNode → emits AugmentationEvent →
 *   returns AugmentationResult to agent → new node becomes schedulable
 */
export interface AugmentationRequest {
  /** ID of the workflow instance being augmented */
  workflowInstanceId: string;

  /** ID of the node making the request (must have augmentable:true) */
  parentNodeId: string;

  /** The agent run that's making this request */
  requestingRunId: string;

  /** What operation to perform */
  operation: AugmentOperation;

  /** The node definition to add */
  node: AugmentedNodeSpec;

  /** Why the agent is adding this node (for audit trail) */
  reason: string;

  /** Priority hint — higher priority nodes get scheduled sooner */
  priority?: number;
}

/**
 * Augmentation operations.
 *
 * insert_child:     Most common. Adds a node as a child of the augmentable parent.
 *                   The child depends_on the parent and feeds into the parent's
 *                   downstream nodes (if gate/join exists).
 *
 * insert_sibling:   Adds a parallel sibling within the same expansion zone.
 *                   Does NOT depend on the requesting node — runs concurrently.
 *
 * insert_sequential: Adds a node that must run AFTER the requesting node completes
 *                    but BEFORE the downstream gate. Useful for "I found something
 *                    that needs checking before we proceed."
 *
 * replace_self:     The agent replaces itself with a subworkflow (fan-out pattern).
 *                   Original node is marked 'replaced' and the subworkflow takes over.
 */
export type AugmentOperation =
  | 'insert_child'
  | 'insert_sibling'
  | 'insert_sequential'
  | 'replace_self';

/**
 * Specification for a dynamically added node.
 * Subset of the full YAML node schema — only fields relevant at runtime.
 */
export interface AugmentedNodeSpec {
  /** Unique ID for the new node. Engine may prefix with zone/parent for uniqueness. */
  id: string;

  /** Node type (constrained by injection_rules.allowed_types) */
  type: 'agent_task' | 'human_checkpoint' | 'gate' | 'subworkflow';

  /** Human-readable label */
  label: string;

  /** Why this node exists */
  description?: string;

  /** Agent task configuration (when type=agent_task) */
  agent_task?: {
    source?: AgentSource;
    model?: string;
    prompt: string;
    tools_allowed?: string[];
    tools_denied?: string[];
    max_turns?: number;
    cost_limit_usd?: number;
    workspace?: {
      cwd?: string;
      git_branch?: string;
    };
    /** Files/context to inject */
    context_files?: string[];
    /** Environment variables */
    environment?: Record<string, string>;
  };

  /** Human checkpoint configuration (when type=human_checkpoint) */
  checkpoint?: {
    assignee?: string;
    message: string;
    actions: Array<{
      id: string;
      label: string;
      style?: 'primary' | 'danger' | 'default';
    }>;
    timeout?: string;
    timeout_action?: string;
  };

  /** Gate configuration (when type=gate) */
  gate?: {
    mode: 'all_of' | 'any_of' | 'expression';
    conditions?: string[];
    expression?: string;
  };

  /** Subworkflow configuration (when type=subworkflow) */
  subworkflow?: {
    workflow_ref: string;
    input_mapping?: Record<string, string>;
    output_mapping?: Record<string, string>;
  };

  /** Timeout override (must be within parent's remaining budget) */
  timeout?: string;

  /** Outputs this node will produce */
  outputs?: Record<string, { type: string; description?: string }>;

  /** Tags for filtering/viz */
  tags?: string[];
}

// ═══════════════════════════════════════════════════════════════════════════════
// 2. AUGMENTATION RESULT — Response to the requesting agent
// ═══════════════════════════════════════════════════════════════════════════════

export interface AugmentationResult {
  /** Whether the augmentation was accepted */
  accepted: boolean;

  /** Final node ID (may be prefixed/modified from request) */
  nodeId: string | null;

  /** If rejected, why */
  rejectionReason?: AugmentationRejection;

  /** If accepted but requires approval, this is true */
  pendingApproval: boolean;

  /** The edges that were created/modified */
  edgesCreated: AugmentedEdge[];

  /** Updated constraint state (how much budget/capacity remains) */
  remainingBudget: {
    nodesRemaining: number;
    costRemainingUsd: number | null;
    timeoutRemainingMs: number | null;
  };
}

export type AugmentationRejection =
  | { kind: 'max_children_exceeded'; current: number; max: number }
  | { kind: 'type_not_allowed'; requested: string; allowed: string[] }
  | { kind: 'cost_budget_exceeded'; requested: number; remaining: number }
  | { kind: 'timeout_exceeded'; requested: number; remaining: number }
  | { kind: 'source_not_allowed'; source: string; allowed: string[] }
  | { kind: 'parent_not_augmentable'; nodeId: string }
  | { kind: 'workflow_not_running'; status: string }
  | { kind: 'approval_required'; approver: string }
  | { kind: 'cycle_detected'; path: string[] }
  | { kind: 'node_id_conflict'; existingId: string };

// ═══════════════════════════════════════════════════════════════════════════════
// 3. EDGE RECALCULATION — How the DAG is rewired
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * When a node is augmented into the DAG, edges are recalculated based on
 * the operation type and the expansion zone boundaries.
 *
 * EDGE WIRING RULES:
 *
 * insert_child:
 *   - New edge: parent → new_node
 *   - If parent has a downstream gate with join:all, add: new_node → gate
 *   - The gate's expected input count increments
 *
 * insert_sibling:
 *   - New edge: parent's upstream → new_node (same dependencies as parent)
 *   - If join/gate exists downstream: new_node → gate
 *   - No edge between parent and new_node (they're parallel)
 *
 * insert_sequential:
 *   - New edge: parent → new_node → parent's first downstream
 *   - The original parent→downstream edge is REPLACED, not duplicated
 *
 * replace_self:
 *   - Parent node marked as 'replaced' (not failed/skipped — special state)
 *   - All parent's incoming edges → subworkflow entry node
 *   - Subworkflow exit node → all parent's outgoing edges
 *   - Parent's AgentRun continues (it manages the subworkflow)
 */
export interface AugmentedEdge {
  from: string;
  to: string;
  kind: 'augmented';   // Distinguished from static edges for visualization
  condition?: {
    status?: 'completed' | 'failed' | 'any_terminal';
    expression?: string;
  };
  createdAt: number;
  createdBy: string;   // The run ID that triggered this edge creation
}

// ═══════════════════════════════════════════════════════════════════════════════
// 4. AUGMENTED NODE STATE — Runtime tracking
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * An augmented node in the live workflow.
 * Distinguished from static nodes for visualization (dashed border, different color).
 */
export interface AugmentedNode {
  /** The node spec provided by the agent */
  spec: AugmentedNodeSpec;

  /** Metadata about the augmentation */
  meta: {
    /** When was this node added */
    augmentedAt: number;
    /** Which workflow node's agent added it */
    augmentedBy: string;
    /** Which AgentRun made the request */
    requestingRunId: string;
    /** The operation used */
    operation: AugmentOperation;
    /** Agent's stated reason */
    reason: string;
  };

  /** Current state */
  state: AugmentedNodeState;

  /** Bound AgentRun (once execution starts) */
  boundRunId: string | null;

  /** Approval tracking (when injection_rules.auto_approve=false) */
  approval: {
    required: boolean;
    status: 'pending' | 'approved' | 'rejected';
    approver: string | null;
    approvedAt: number | null;
    comment: string | null;
  } | null;

  /** Edges connected to this node */
  incomingEdges: AugmentedEdge[];
  outgoingEdges: AugmentedEdge[];
}

export type AugmentedNodeState =
  | 'pending_approval'   // Waiting for human to approve injection
  | 'approved'           // Approved, waiting for dependencies
  | 'ready'              // Dependencies satisfied, can be scheduled
  | 'running'            // Agent executing
  | 'completed'          // Done successfully
  | 'failed'             // Failed (may retry per parent's policy)
  | 'cancelled'          // Cancelled (workflow cancelled, or parent revoked)
  | 'rejected';          // Human rejected the augmentation

// ═══════════════════════════════════════════════════════════════════════════════
// 5. CONSTRAINT ENFORCEMENT
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * The engine validates every augmentation request against injection_rules.
 * This is the validation pipeline (executed synchronously before response).
 */
export interface AugmentationValidator {
  validate(
    request: AugmentationRequest,
    parentNode: { augmentable: boolean; injection_rules: InjectionRules },
    currentState: AugmentationZoneState
  ): AugmentationResult;
}

/** injection_rules from YAML schema */
export interface InjectionRules {
  max_children: number;
  allowed_types: string[];
  auto_approve: boolean;
  requires_approval_from?: string;
  allowed_sources?: string[];
  cost_budget_usd?: number;
  inherit_timeout: boolean;
}

/** Current state of an augmentation zone (accumulated across all injections) */
export interface AugmentationZoneState {
  /** How many nodes have been added so far */
  currentChildCount: number;
  /** Total cost consumed by augmented nodes so far */
  totalCostUsd: number;
  /** Total time consumed by augmented nodes so far (ms) */
  totalElapsedMs: number;
  /** IDs of all augmented nodes in this zone */
  augmentedNodeIds: string[];
  /** Whether the parent node is still running */
  parentStillRunning: boolean;
}

/**
 * Validation pipeline steps (in order):
 *
 * 1. Check workflow is in 'running' state
 * 2. Check parent node has augmentable:true
 * 3. Check node type is in allowed_types
 * 4. Check source is in allowed_sources (if specified)
 * 5. Check max_children not exceeded
 * 6. Check cost_budget_usd not exceeded (estimated from node config)
 * 7. Check timeout wouldn't exceed parent's remaining time
 * 8. Check node ID doesn't conflict with existing nodes
 * 9. Check no cycle would be created (validate DAG property)
 * 10. If auto_approve=false, set pendingApproval=true
 */

// ═══════════════════════════════════════════════════════════════════════════════
// 6. WORKFLOW ENGINE INTEGRATION
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * The workflow_augment tool exposed to agents during workflow execution.
 * This is the API surface agents interact with.
 */
export interface WorkflowAugmentTool {
  /**
   * Add a node to the current workflow.
   *
   * @example Agent discovers 5 test suites, adds a node per suite:
   * ```
   * for suite in discovered_suites:
   *   workflow_augment({
   *     operation: 'insert_child',
   *     node: {
   *       id: `test-${suite.name}`,
   *       type: 'agent_task',
   *       label: `Run ${suite.name} tests`,
   *       agent_task: {
   *         prompt: `Run test suite: ${suite.path}`,
   *         tools_allowed: ['bash', 'read'],
   *         max_turns: 10,
   *       }
   *     },
   *     reason: `Discovered test suite: ${suite.name} (${suite.test_count} tests)`
   *   })
   * ```
   */
  augment(request: Omit<AugmentationRequest, 'workflowInstanceId' | 'parentNodeId' | 'requestingRunId'>): AugmentationResult;

  /**
   * Query current augmentation zone state.
   * Agents can check remaining budget before augmenting.
   */
  getZoneState(): AugmentationZoneState;

  /**
   * List existing augmented nodes in this zone.
   * Useful for deduplication (don't add a node that already exists).
   */
  listAugmentedNodes(): Array<{ id: string; label: string; state: AugmentedNodeState }>;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 7. REAL-TIME VISUALIZATION EVENTS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * Events emitted for the frontend to update the DAG visualization in real-time.
 * Sent over WebSocket alongside existing WorkflowEvents.
 */
export type AugmentationEvent =
  | AugmentationRequestedEvent
  | AugmentationAcceptedEvent
  | AugmentationRejectedEvent
  | AugmentationApprovedEvent
  | AugmentationNodeStartedEvent
  | AugmentationNodeCompletedEvent;

interface BaseAugmentationEvent {
  kind: string;
  workflowInstanceId: string;
  timestamp: number;
}

/** Agent requested an augmentation (shown as "pending" in viz) */
export interface AugmentationRequestedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_requested';
  request: AugmentationRequest;
}

/** Engine accepted the augmentation — new node appears in DAG */
export interface AugmentationAcceptedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_accepted';
  node: AugmentedNode;
  edges: AugmentedEdge[];
}

/** Engine rejected the augmentation (constraint violation) */
export interface AugmentationRejectedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_rejected';
  requestNodeId: string;
  reason: AugmentationRejection;
}

/** Human approved a pending augmentation */
export interface AugmentationApprovedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_approved';
  nodeId: string;
  approver: string;
}

/** Augmented node started executing */
export interface AugmentationNodeStartedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_node_started';
  nodeId: string;
  runId: string;
}

/** Augmented node completed */
export interface AugmentationNodeCompletedEvent extends BaseAugmentationEvent {
  kind: 'augmentation_node_completed';
  nodeId: string;
  status: 'completed' | 'failed' | 'cancelled';
  outputs?: Record<string, unknown>;
}

// ═══════════════════════════════════════════════════════════════════════════════
// 8. REST API ENDPOINTS
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * FastAPI endpoints for augmentation management.
 *
 * POST /api/workflows/{instance_id}/augment
 *   Body: AugmentationRequest
 *   Response: AugmentationResult
 *   Auth: Agent token (validated against requesting run)
 *
 * GET /api/workflows/{instance_id}/augmented-nodes
 *   Response: AugmentedNode[]
 *   Query params: ?parent_node_id=X&state=pending
 *
 * POST /api/workflows/{instance_id}/augmented-nodes/{node_id}/approve
 *   Body: { approved: boolean; comment?: string }
 *   Response: { success: boolean }
 *   Auth: Must be the approver specified in injection_rules
 *
 * DELETE /api/workflows/{instance_id}/augmented-nodes/{node_id}
 *   Response: { success: boolean }
 *   Auth: Workflow owner or the requesting agent
 *   Note: Only allowed when node is pending/approved (not running/completed)
 *
 * WebSocket /ws/workflows/{instance_id}/augmentation
 *   Emits: AugmentationEvent stream
 *   Used by: Frontend DAG visualization for real-time node appearance
 */

// ═══════════════════════════════════════════════════════════════════════════════
// 9. EXAMPLE: Complete Augmentation Flow
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * SCENARIO: "Deploy Microservice" workflow, test discovery phase.
 *
 * YAML declares:
 * ```yaml
 * - id: discover-tests
 *   type: agent_task
 *   label: "Discover Test Suites"
 *   augmentable: true
 *   injection_rules:
 *     max_children: 10
 *     allowed_types: [agent_task]
 *     auto_approve: true
 *     cost_budget_usd: 5.00
 *     inherit_timeout: true
 *   config:
 *     prompt: |
 *       Scan the repository for test suites.
 *       For each suite found, use workflow_augment to add a test runner node.
 *     tools_allowed: [bash, read, workflow_augment]
 *
 * - id: test-gate
 *   type: gate
 *   label: "All Tests Pass"
 *   depends_on:
 *     - node: discover-tests
 *       join: all   # Waits for parent AND all augmented children
 *   config:
 *     mode: all_of
 *     conditions:
 *       - ${{ nodes.discover-tests.status == 'completed' }}
 *       # Gate auto-includes augmented children in its join set
 * ```
 *
 * RUNTIME FLOW:
 *
 * 1. Engine starts node "discover-tests", spawns AgentRun
 *
 * 2. Agent scans repo, finds 3 test suites:
 *    - unit-tests (jest, 200 tests)
 *    - integration-tests (playwright, 50 tests)
 *    - e2e-tests (cypress, 30 tests)
 *
 * 3. Agent calls workflow_augment 3 times:
 *
 *    Request 1:
 *    {
 *      operation: 'insert_child',
 *      node: {
 *        id: 'run-unit-tests',
 *        type: 'agent_task',
 *        label: 'Run Unit Tests (Jest)',
 *        agent_task: {
 *          prompt: 'Run: npx jest --ci --coverage. Report pass/fail and coverage %.',
 *          tools_allowed: ['bash', 'read'],
 *          max_turns: 10,
 *          cost_limit_usd: 1.00,
 *        },
 *        outputs: { passed: { type: 'boolean' }, coverage: { type: 'number' } },
 *      },
 *      reason: 'Discovered Jest test suite with 200 tests in /tests/unit'
 *    }
 *
 *    Engine validates: ✓ augmentable, ✓ type allowed, ✓ 1/10 children, ✓ $1 < $5 budget
 *    Engine creates edges: discover-tests → run-unit-tests → test-gate
 *    Engine returns: { accepted: true, nodeId: 'run-unit-tests', ... }
 *    Engine emits: AugmentationAcceptedEvent (frontend adds node to viz)
 *
 *    (Repeat for integration and e2e tests)
 *
 * 4. Agent finishes its own work (discover-tests → completed)
 *
 * 5. Engine schedules the 3 augmented nodes (all dependencies met)
 *    Each spawns its own AgentRun (visible in topology)
 *
 * 6. All 3 complete → test-gate's join:all is satisfied (parent + 3 children)
 *    → workflow proceeds to next phase
 *
 * RESULTING DAG (visualized):
 *
 *   [discover-tests] ──→ [run-unit-tests]* ──→ [test-gate] → ...
 *                    ├──→ [run-integration-tests]* ──┘
 *                    └──→ [run-e2e-tests]* ──────────┘
 *
 *   * = augmented nodes (rendered with dashed border in frontend)
 */

// ═══════════════════════════════════════════════════════════════════════════════
// 10. VISUALIZATION TREATMENT
// ═══════════════════════════════════════════════════════════════════════════════

/**
 * How augmented nodes appear differently in the Cytoscape.js DAG view:
 *
 * STATIC nodes:      Solid border, full opacity, positioned per YAML ui hints
 * AUGMENTED nodes:   Dashed border, slightly muted, auto-positioned near parent
 * PENDING APPROVAL:  Pulsing amber glow, approval badge overlay
 * REJECTED:          Red dashed border, greyed out, strike-through label
 *
 * EDGES from augmented nodes: Dashed line style, lighter color than static edges
 *
 * EXPANSION ZONES: When a node has augmentable:true and injection_rules,
 * the viz shows a faint dashed bounding box around the potential expansion area,
 * with a "+N available" badge showing remaining capacity.
 *
 * ANIMATION: When an augmentation event arrives via WebSocket, the new node
 * animates into position (spring physics) rather than appearing instantly.
 * This gives users visual awareness of the DAG growing.
 *
 * TIMELINE VIEW: Augmented nodes show an "injected by X" annotation with
 * timestamp, linking back to the parent node's execution that requested it.
 */
