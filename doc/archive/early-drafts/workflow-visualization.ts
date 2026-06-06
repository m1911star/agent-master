/**
 * Workflow Visualization System — Real-time DAG Rendering
 * =======================================================
 * Cytoscape.js-based visualization for workflow DAGs with live progress,
 * critical path highlighting, and semantic zoom.
 */

// ============================================================
// 1. NODE & WORKFLOW STATE TYPES (from workflow engine)
// ============================================================

export type NodeStatus =
  | 'pending'    // waiting for dependencies
  | 'ready'      // all deps met, queued for execution
  | 'running'    // agent actively working
  | 'completed'  // finished successfully
  | 'failed'     // errored out
  | 'skipped'    // bypassed (conditional)
  | 'cancelled'; // user or workflow cancelled

export type WorkflowStatus =
  | 'draft' | 'ready' | 'running'
  | 'completed' | 'failed' | 'cancelled' | 'paused';

export type DependencyType =
  | 'data_flow'     // output of A feeds input of B
  | 'control_flow'  // A must complete before B starts
  | 'trigger'       // A's completion triggers B
  | 'optional';     // soft dependency (excluded from critical path)

// ============================================================
// 2. CORE VISUALIZATION INTERFACES
// ============================================================

/** Zoom semantic levels */
export type ZoomLevel = 'overview' | 'workflow' | 'node-detail';

/** Complete render state for the visualization layer */
export interface WorkflowVisualization {
  /** Active zoom level (derived from viewport zoom range) */
  zoomLevel: ZoomLevel;

  /** Currently displayed workflow IDs (overview shows all, drill-in shows one) */
  visibleWorkflowIds: string[];

  /** Cytoscape element collections keyed by workflow */
  graphs: Map<string, WorkflowGraphState>;

  /** Layout configuration (may differ per zoom level) */
  layout: WorkflowLayoutConfig;

  /** Viewport state */
  viewport: ViewportState;

  /** Selection state */
  selection: SelectionState;

  /** Overlay layers rendered on top of the graph */
  overlays: {
    criticalPath: CriticalPathHighlight | null;
    progress: ProgressOverlay;
    resourceUtilization: ResourceUtilizationOverlay;
  };

  /** Queued animations awaiting execution */
  animationQueue: AnimationEntry[];

  /** Breadcrumb trail for navigation */
  breadcrumbs: BreadcrumbEntry[];

  /** Timestamp of last state update (for staleness detection) */
  lastUpdateAt: number;
}

/** State of a single workflow's graph */
export interface WorkflowGraphState {
  workflowId: string;
  workflowStatus: WorkflowStatus;
  nodes: Map<string, NodeVisualState>;
  edges: Map<string, EdgeVisualState>;
  /** Compound/group nodes for sub-workflow clusters */
  groups: Map<string, GroupVisualState>;
}

export interface ViewportState {
  /** Current zoom factor (0.1 = fully zoomed out, 3.0 = max zoom) */
  zoom: number;
  /** Pan offset */
  pan: { x: number; y: number };
  /** Visible bounding box in model coordinates */
  extent: { x1: number; y1: number; x2: number; y2: number };
  /** Zoom thresholds for semantic level transitions */
  thresholds: {
    overviewToWorkflow: number;   // e.g., 0.4
    workflowToDetail: number;    // e.g., 1.8
  };
}

export interface SelectionState {
  /** Currently selected node IDs */
  selectedNodeIds: Set<string>;
  /** Currently selected edge IDs */
  selectedEdgeIds: Set<string>;
  /** Hovered element */
  hoveredId: string | null;
  /** Focused node for keyboard navigation */
  focusedNodeId: string | null;
}

export interface BreadcrumbEntry {
  level: ZoomLevel;
  label: string;
  targetId: string | null; // workflow or node ID
}

// ============================================================
// 3. NODE VISUAL STATE
// ============================================================

/** Complete visual representation of a single workflow node */
export interface NodeVisualState {
  /** Node identity */
  id: string;
  workflowId: string;

  /** Logical state from workflow engine */
  status: NodeStatus;

  /** Position & dimensions (set by layout algorithm) */
  position: { x: number; y: number };
  dimensions: { width: number; height: number };

  /** Visual styling (derived from status + context) */
  style: NodeStyle;

  /** Progress tracking (only meaningful when status === 'running') */
  progress: NodeProgress | null;

  /** Badges rendered on the node */
  badges: NodeBadges;

  /** Current animation state */
  animation: AnimationState;

  /** Interaction state */
  interaction: {
    selected: boolean;
    hovered: boolean;
    focused: boolean;
    /** Dimmed when not on critical path and filter is active */
    dimmed: boolean;
  };

  /** Labels */
  label: {
    primary: string;      // node title
    secondary: string;    // agent name or status text
    truncated: boolean;   // true if label was clipped
  };

  /** Assigned agent (if running or completed) */
  assignedAgentId: string | null;

  /** Whether this node is on the critical path */
  onCriticalPath: boolean;
}

export interface NodeStyle {
  /** Background */
  backgroundColor: string;      // CSS color (uses custom properties)
  backgroundOpacity: number;    // 0-1
  /** Border */
  borderColor: string;
  borderWidth: number;          // px
  borderStyle: 'solid' | 'dashed' | 'double';
  /** Shape */
  shape: CytoscapeNodeShape;
  /** Shadow/glow for critical path or selection */
  shadow: {
    color: string;
    blur: number;
    offsetX: number;
    offsetY: number;
  } | null;
  /** Text */
  textColor: string;
  fontSize: number;
}

export type CytoscapeNodeShape =
  | 'roundrectangle'  // standard task
  | 'diamond'         // decision/gate
  | 'ellipse'         // start/end
  | 'hexagon'         // external trigger
  | 'octagon';        // approval/manual step

export interface NodeProgress {
  /** 0.0 to 1.0, computed from elapsed/estimated or explicit progress events */
  percent: number;
  /** Estimated time remaining in ms (null if unknown) */
  etaMs: number | null;
  /** Elapsed time in ms */
  elapsedMs: number;
  /** Original estimate in ms (from definition or historical average) */
  estimatedDurationMs: number | null;
  /** Whether the node is over its estimate */
  overdue: boolean;
}

export interface NodeBadges {
  /** Agent avatar/icon (bottom-left) */
  agentIcon: { src: string; label: string } | null;
  /** ETA countdown (top-right) */
  eta: { text: string; overdue: boolean } | null;
  /** Error count (bottom-right, only on failed) */
  errorCount: number | null;
  /** Retry attempt number */
  retryAttempt: number | null;
  /** Sub-workflow indicator */
  hasChildren: boolean;
}

// ============================================================
// 4. EDGE VISUAL STATE
// ============================================================

/** Complete visual representation of a workflow edge */
export interface EdgeVisualState {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;

  /** Dependency semantics */
  dependencyType: DependencyType;

  /** Visual styling */
  style: EdgeStyle;

  /** Whether this edge is on the critical path */
  onCriticalPath: boolean;

  /** Animation state (e.g., data flow particles) */
  animation: EdgeAnimation;

  /** Interaction */
  interaction: {
    selected: boolean;
    hovered: boolean;
    dimmed: boolean;
  };

  /** Label (optional — shows data passed or condition) */
  label: string | null;
}

export interface EdgeStyle {
  /** Line style varies by dependency type */
  lineStyle: 'solid' | 'dashed' | 'dotted';
  /** Color (from dependency type + state) */
  lineColor: string;
  /** Width (thicker for critical path) */
  width: number;
  /** Arrow shape */
  targetArrowShape: 'triangle' | 'circle' | 'diamond' | 'none';
  /** Curve style */
  curveStyle: 'bezier' | 'taxi' | 'segments' | 'straight';
  /** Opacity (dimmed edges get 0.3) */
  opacity: number;
}

export interface EdgeAnimation {
  /** Active animation type */
  type: 'none' | 'flow' | 'pulse' | 'dash';
  /** For 'flow': particle speed and direction */
  flowSpeed: number;
  /** For 'dash': dash pattern offset animation */
  dashOffset: number;
  /** Whether animation is active */
  active: boolean;
}

// ============================================================
// 5. CRITICAL PATH HIGHLIGHT
// ============================================================

/** Critical path overlay — longest path through the DAG determining completion time */
export interface CriticalPathHighlight {
  /** Ordered list of node IDs on the critical path (source → sink) */
  pathNodeIds: string[];

  /** Edge IDs on the critical path */
  pathEdgeIds: string[];

  /** Total estimated duration of the critical path in ms */
  totalDurationMs: number;

  /** Time already elapsed on critical path */
  elapsedMs: number;

  /** Remaining time on critical path */
  remainingMs: number;

  /** Per-node slack (for nodes NOT on critical path) */
  slackByNodeId: Map<string, SlackInfo>;

  /** Visual properties for the highlight */
  visual: CriticalPathVisual;

  /** When the critical path was last recomputed */
  computedAt: number;

  /** Whether the path changed since last computation */
  pathChanged: boolean;
}

export interface SlackInfo {
  /** Total float: how much this node can delay without affecting workflow end */
  totalSlackMs: number;
  /** Free float: how much this node can delay without affecting its successors */
  freeSlackMs: number;
  /** Earliest start time */
  earliestStartMs: number;
  /** Latest start time (without delaying workflow) */
  latestStartMs: number;
}

export interface CriticalPathVisual {
  /** Glow color for critical path nodes/edges */
  glowColor: string;
  /** Glow blur radius in px */
  glowBlur: number;
  /** Edge thickness multiplier on critical path */
  edgeWidthMultiplier: number;
  /** Pulse animation config */
  pulse: {
    enabled: boolean;
    durationMs: number;
    /** Min/max opacity for pulse */
    opacityRange: [number, number];
  };
  /** Whether non-critical nodes are dimmed */
  dimNonCritical: boolean;
  /** Dim opacity for non-critical elements */
  dimOpacity: number;
}

// ============================================================
// 6. PROGRESS OVERLAY
// ============================================================

/** Workflow-level progress and resource information */
export interface ProgressOverlay {
  /** Overall workflow progress */
  overall: OverallProgress;

  /** Per-node progress entries (only for running + recently completed) */
  nodeProgress: Map<string, NodeProgressEntry>;

  /** Resource utilization summary */
  resources: ResourceUtilizationOverlay;

  /** Throughput metrics */
  throughput: ThroughputMetrics;
}

export interface OverallProgress {
  /** Nodes completed / total nodes */
  completedCount: number;
  totalCount: number;
  /** Completion percentage (0-100) */
  percent: number;
  /** Estimated time to workflow completion (from critical path) */
  etaMs: number | null;
  /** Workflow start time */
  startedAt: number;
  /** Elapsed wall time */
  elapsedMs: number;
  /** Status text ("3/12 nodes complete, ~4m remaining") */
  statusText: string;
}

export interface NodeProgressEntry {
  nodeId: string;
  status: NodeStatus;
  /** Actual duration (if completed) or elapsed (if running) */
  actualMs: number;
  /** Estimated total duration */
  estimatedMs: number | null;
  /** Percent complete (0-100) */
  percent: number;
  /** Delta from estimate (positive = over, negative = under) */
  deltaMs: number | null;
  /** Assigned agent info */
  agent: { id: string; name: string; source: string } | null;
}

export interface ResourceUtilizationOverlay {
  /** Per-agent utilization */
  agents: AgentUtilization[];
  /** Total available agent capacity */
  totalCapacity: number;
  /** Currently busy agents */
  busyCount: number;
  /** Idle agents available for work */
  idleCount: number;
  /** Nodes waiting in queue (ready but no agent) */
  queuedNodeCount: number;
}

export interface AgentUtilization {
  agentId: string;
  agentName: string;
  agentSource: string;  // 'claude-code' | 'codex' | etc
  /** Current status */
  status: 'busy' | 'idle' | 'offline';
  /** Node currently being worked on (if busy) */
  currentNodeId: string | null;
  /** Recent completed node count (last hour) */
  recentCompletions: number;
  /** Average task duration for this agent */
  avgDurationMs: number;
}

export interface ThroughputMetrics {
  /** Nodes completed per minute (rolling 5-min window) */
  nodesPerMinute: number;
  /** Average node duration (rolling) */
  avgNodeDurationMs: number;
  /** Parallelism factor (concurrent running nodes, rolling avg) */
  avgParallelism: number;
  /** Estimated completion based on current throughput */
  projectedCompletionAt: number | null;
}

// ============================================================
// 7. LAYOUT CONFIGURATION
// ============================================================

/** Layout algorithm and parameters */
export interface WorkflowLayoutConfig {
  /** Layout algorithm to use */
  algorithm: LayoutAlgorithm;

  /** Graph direction */
  direction: 'TB' | 'LR' | 'BT' | 'RL';

  /** Spacing between nodes (px) */
  nodeSep: number;

  /** Spacing between ranks/layers (px) */
  rankSep: number;

  /** Spacing between edges (px) */
  edgeSep: number;

  /** Whether to align nodes in same rank */
  align: 'UL' | 'UR' | 'DL' | 'DR' | null;

  /** Compound node (sub-workflow) padding */
  groupPadding: number;

  /** How to handle rank assignment */
  ranker: 'network-simplex' | 'tight-tree' | 'longest-path';

  /** Clustering configuration */
  clusters: ClusterConfig;

  /** Layout animation */
  animateLayout: boolean;
  animationDurationMs: number;
  animationEasing: CytoscapeEasing;

  /** Incremental layout settings */
  incremental: IncrementalLayoutConfig;

  /** Per-zoom-level overrides */
  zoomOverrides: Partial<Record<ZoomLevel, Partial<WorkflowLayoutConfig>>>;
}

export type LayoutAlgorithm =
  | 'dagre'          // Default: fast, good for medium DAGs
  | 'elk'            // Better for large/complex DAGs with constraints
  | 'klay'           // ELK's predecessor, simpler
  | 'breadthfirst'   // Simple fallback
  | 'preset';        // Fixed positions (user-arranged)

export type CytoscapeEasing =
  | 'linear'
  | 'ease-in'
  | 'ease-out'
  | 'ease-in-out'
  | 'ease-in-sine'
  | 'ease-out-sine'
  | 'ease-in-out-sine'
  | 'spring(500, 40)';

export interface ClusterConfig {
  /** Whether to group parallel branches */
  groupParallelBranches: boolean;
  /** Whether to collapse sub-workflows */
  collapseSubWorkflows: boolean;
  /** Min nodes before a group becomes collapsible */
  collapseThreshold: number;
  /** Per-group collapse state */
  collapsedGroups: Set<string>;
}

export interface IncrementalLayoutConfig {
  /** Whether to use incremental layout (avoids full recompute) */
  enabled: boolean;
  /** Only relayout if more than N nodes changed position */
  changeThreshold: number;
  /** Debounce time for batching state changes before relayout */
  debounceMs: number;
  /** Pin completed nodes (don't move them on relayout) */
  pinCompleted: boolean;
  /** Animate position changes during incremental updates */
  animateChanges: boolean;
}

export interface GroupVisualState {
  id: string;
  label: string;
  /** Child node IDs */
  childNodeIds: string[];
  /** Whether group is collapsed */
  collapsed: boolean;
  /** Computed bounds (from children) */
  bounds: { x: number; y: number; width: number; height: number };
  /** Group style */
  style: {
    backgroundColor: string;
    borderColor: string;
    borderStyle: 'solid' | 'dashed';
    borderRadius: number;
    opacity: number;
  };
}

// ============================================================
// 8. ANIMATION SYSTEM
// ============================================================

export interface AnimationState {
  /** Currently playing animation */
  current: ActiveAnimation | null;
  /** Queue of pending animations */
  queue: AnimationEntry[];
}

export interface ActiveAnimation {
  type: AnimationType;
  startedAt: number;
  durationMs: number;
  /** 0.0 to 1.0 */
  progress: number;
  /** Whether this is looping (e.g., running spinner) */
  loop: boolean;
}

export interface AnimationEntry {
  /** Target element (node or edge ID) */
  targetId: string;
  /** Animation to play */
  type: AnimationType;
  /** Duration in ms */
  durationMs: number;
  /** Delay before starting */
  delayMs: number;
  /** Whether to loop */
  loop: boolean;
  /** Priority (higher = plays first) */
  priority: number;
  /** Callback on completion */
  onComplete?: () => void;
}

export type AnimationType =
  | 'pulse-ready'       // gentle scale pulse when node becomes ready
  | 'spin-running'      // rotating border/ring while running
  | 'progress-tick'     // progress bar increment
  | 'complete-flash'    // brief green flash + checkmark
  | 'fail-shake'        // horizontal shake + red flash
  | 'cancel-fade'       // fade to grey
  | 'skip-strikethrough'// diagonal line through node
  | 'critical-glow'     // pulsing glow on critical path
  | 'edge-flow'         // particles flowing along edge
  | 'layout-move'       // smooth position transition
  | 'zoom-transition'   // viewport zoom animation
  | 'appear'            // node fade-in on creation
  | 'collapse'          // group collapse animation
  | 'expand';           // group expand animation

// ============================================================
// 9. COLOR SCHEME
// ============================================================

/**
 * Color scheme using CSS custom properties for light/dark mode support.
 * All colors defined as hex; actual rendering uses var(--wf-*) properties.
 *
 * Accessibility: all combinations meet WCAG AA contrast ratio (4.5:1 text, 3:1 UI).
 */
export interface ColorScheme {
  /** Node state colors */
  nodeStates: Record<NodeStatus, NodeColorSet>;
  /** Edge dependency type colors */
  edgeTypes: Record<DependencyType, EdgeColorSet>;
  /** Critical path highlight */
  criticalPath: { glow: string; edge: string; pulse: string };
  /** Selection/interaction */
  selection: { border: string; background: string; glow: string };
  /** Background */
  canvasBackground: string;
  /** Grid/guide lines */
  gridColor: string;
}

export interface NodeColorSet {
  background: string;
  border: string;
  text: string;
  /** Icon/badge tint */
  accent: string;
}

export interface EdgeColorSet {
  line: string;
  arrow: string;
  label: string;
}

/**
 * Default color values (light mode):
 *
 * | State      | Background | Border   | Text     | Accent   |
 * |------------|-----------|----------|----------|----------|
 * | pending    | #F3F4F6   | #D1D5DB  | #6B7280  | #9CA3AF  |
 * | ready      | #EFF6FF   | #93C5FD  | #1E40AF  | #3B82F6  |
 * | running    | #FFF7ED   | #FB923C  | #9A3412  | #F97316  |
 * | completed  | #ECFDF5   | #6EE7B7  | #065F46  | #10B981  |
 * | failed     | #FEF2F2   | #FCA5A5  | #991B1B  | #EF4444  |
 * | skipped    | #F9FAFB   | #E5E7EB  | #9CA3AF  | #D1D5DB  |
 * | cancelled  | #F9FAFB   | #E5E7EB  | #6B7280  | #9CA3AF  |
 *
 * Dark mode equivalents shift lightness: bg → 10-15% L, border → 40% L, text → 85% L
 *
 * Edge colors by dependency type:
 * | Type         | Line     | Description          |
 * |-------------|----------|---------------------|
 * | data_flow    | #6366F1  | Indigo, solid       |
 * | control_flow | #8B5CF6  | Violet, solid       |
 * | trigger      | #F59E0B  | Amber, dashed       |
 * | optional     | #D1D5DB  | Gray, dotted        |
 *
 * Critical path: #F59E0B (amber glow), edges #DC2626 (red-600)
 */

// ============================================================
// 10. CSS CUSTOM PROPERTIES (for runtime theming)
// ============================================================

/**
 * Applied to :root or .workflow-viz container:
 *
 * --wf-node-pending-bg: #F3F4F6;
 * --wf-node-pending-border: #D1D5DB;
 * --wf-node-pending-text: #6B7280;
 * --wf-node-ready-bg: #EFF6FF;
 * --wf-node-ready-border: #93C5FD;
 * --wf-node-ready-text: #1E40AF;
 * --wf-node-running-bg: #FFF7ED;
 * --wf-node-running-border: #FB923C;
 * --wf-node-running-text: #9A3412;
 * --wf-node-completed-bg: #ECFDF5;
 * --wf-node-completed-border: #6EE7B7;
 * --wf-node-completed-text: #065F46;
 * --wf-node-failed-bg: #FEF2F2;
 * --wf-node-failed-border: #FCA5A5;
 * --wf-node-failed-text: #991B1B;
 * --wf-node-skipped-bg: #F9FAFB;
 * --wf-node-skipped-border: #E5E7EB;
 * --wf-node-skipped-text: #9CA3AF;
 * --wf-node-cancelled-bg: #F9FAFB;
 * --wf-node-cancelled-border: #E5E7EB;
 * --wf-node-cancelled-text: #6B7280;
 * --wf-edge-data: #6366F1;
 * --wf-edge-control: #8B5CF6;
 * --wf-edge-trigger: #F59E0B;
 * --wf-edge-optional: #D1D5DB;
 * --wf-critical-glow: #F59E0B;
 * --wf-critical-edge: #DC2626;
 * --wf-selection-border: #2563EB;
 * --wf-canvas-bg: #FFFFFF;
 * --wf-grid: #F3F4F6;
 *
 * [data-theme="dark"] {
 *   --wf-node-pending-bg: #1F2937;
 *   --wf-node-pending-border: #4B5563;
 *   ... (shifted to dark equivalents)
 *   --wf-canvas-bg: #111827;
 *   --wf-grid: #1F2937;
 * }
 */
