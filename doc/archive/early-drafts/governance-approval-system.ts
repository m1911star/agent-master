/**
 * Governance & Approval System for Agent Witness
 * ================================================
 * Local-first human-in-the-loop control plane for multi-agent monitoring.
 *
 * Design principles:
 * 1. Local-first: all policy evaluation happens on-device, no cloud dependency
 * 2. Default-deny for destructive ops; default-allow for read-only
 * 3. Agents retain their native permission models; this layer sits above as a unified gate
 * 4. Async approval: agents can queue requests and continue non-blocked work
 * 5. Audit-complete: every decision (auto or human) is logged with full context
 */

// ============================================================================
// 1. PERMISSION TYPES
// ============================================================================

/**
 * Hierarchical permission categories, ordered by risk.
 * Each agent tool call maps to exactly one PermissionCategory.
 */
export type PermissionCategory =
  | 'read'           // file read, search, glob — always allowed
  | 'write'          // file edit, write, patch
  | 'shell'          // bash/exec with non-destructive commands
  | 'shell_destroy'  // rm, git clean, docker rm, DROP TABLE, etc.
  | 'git_push'       // push, force-push, tag push
  | 'git_rewrite'    // rebase, reset --hard, filter-branch
  | 'network'        // HTTP requests, web search, API calls
  | 'network_cost'   // API calls with known $/call (LLM inference, cloud deploy)
  | 'spawn'          // launching subagents or child processes
  | 'system'         // OS-level: install packages, modify PATH, cron, systemd
  | 'secrets'        // access to vault://, .env, credentials
  | 'approval_admin'; // modifying governance rules themselves

/** Risk level drives default policy and UI urgency */
export type RiskLevel = 'none' | 'low' | 'medium' | 'high' | 'critical';

/** Maps category → default risk. Policies override per-context. */
export const DEFAULT_RISK: Record<PermissionCategory, RiskLevel> = {
  read: 'none',
  write: 'low',
  shell: 'medium',
  shell_destroy: 'critical',
  git_push: 'high',
  git_rewrite: 'critical',
  network: 'low',
  network_cost: 'high',
  spawn: 'low',
  system: 'critical',
  secrets: 'high',
  approval_admin: 'critical',
};

/**
 * A specific permission request from an agent.
 * This is the unit that gets evaluated by the policy engine.
 */
export interface PermissionRequest {
  id: string;                          // uuid v7 (time-sortable)
  timestamp: number;                   // epoch ms

  // Source identification
  runId: string;                       // AgentRun.id from data model
  source: AgentSource;                 // 'claude-code' | 'codex' | ...
  agentLabel: string;                  // human-readable: "Claude (main)" or "Codex (feature-auth)"
  turnId: string | null;               // which turn triggered this

  // What's being requested
  category: PermissionCategory;
  action: string;                      // normalized: 'git push origin main', 'rm -rf node_modules'
  toolName: string;                    // raw tool: 'Bash', 'Edit', 'WebSearch'
  toolInput: unknown;                  // full input for audit (scrubbed of secrets in UI)
  inputPreview: string;                // 120-char safe summary

  // Context for decision
  risk: RiskLevel;                     // computed by policy engine
  workspace: {
    cwd: string;
    gitBranch: string | null;
    gitRepo: string | null;
    isDirty: boolean;                  // uncommitted changes exist
  };
  estimatedCost: CostEstimate | null;  // for network_cost category
  affectedPaths: string[];             // files that would be modified/deleted

  // State
  status: ApprovalStatus;
  decision: ApprovalDecision | null;
}

export type ApprovalStatus =
  | 'pending'      // waiting for human or policy evaluation
  | 'approved'     // allowed to proceed
  | 'denied'       // blocked
  | 'expired'      // timed out waiting for approval
  | 'superseded';  // newer request replaced this one

export interface ApprovalDecision {
  decidedAt: number;
  decidedBy: DecisionSource;
  reason: string | null;               // human note or policy rule name
  expiresAt: number | null;            // one-shot or time-boxed grant
  scope: DecisionScope;                // how broadly this applies
}

export type DecisionSource =
  | { type: 'human'; userId: string }
  | { type: 'policy'; ruleId: string; ruleName: string }
  | { type: 'escalation_timeout' };    // auto-deny after TTL

/** How broadly a human approval applies */
export interface DecisionScope {
  /** Apply only to this exact request */
  oneShot: boolean;
  /** Apply to all matching requests in this run */
  forRun: boolean;
  /** Apply to all matching requests in this session (until restart) */
  forSession: boolean;
  /** Persist as a new auto-approve rule */
  persistAsRule: boolean;
}

export interface CostEstimate {
  currency: 'USD';
  min: number;
  max: number;
  model: string | null;       // which model is being called
  tokensEstimate: number | null;
}


// ============================================================================
// 2. APPROVAL UX
// ============================================================================

/**
 * The approval UI is surfaced in three contexts:
 * 1. Witness dashboard (web) — banner/modal for pending approvals
 * 2. System notification (macOS Notification Center) — for background agents
 * 3. Terminal inline (for agents that support stdin approval like Claude Code)
 *
 * All three consume the same ApprovalPrompt and return ApprovalResponse.
 */

export interface ApprovalPrompt {
  request: PermissionRequest;

  // Rich context for the human
  context: {
    /** What the agent said it's trying to do (from assistant message before tool call) */
    agentIntent: string | null;
    /** Recent actions by this agent (last 5 tool calls) */
    recentActions: Array<{ tool: string; preview: string; timestamp: number }>;
    /** How many times this exact pattern was approved before */
    priorApprovalCount: number;
    /** Suggested action based on prior decisions */
    suggestion: 'approve' | 'deny' | null;
  };

  // UX control
  urgency: 'blocking' | 'queued';      // blocking = agent is waiting; queued = agent moved on
  expiresAt: number;                    // when this auto-denies
  quickActions: QuickAction[];          // pre-built response buttons
}

export interface QuickAction {
  label: string;                       // "Allow once", "Allow for this run", "Always allow git push to main"
  response: ApprovalResponse;
  hotkey: string | null;               // keyboard shortcut in terminal/web
}

export interface ApprovalResponse {
  requestId: string;
  approved: boolean;
  scope: DecisionScope;
  reason: string | null;               // optional human note
  /** If approved with modification (e.g., "allow but redirect to staging") */
  modification: Record<string, unknown> | null;
}

/**
 * Notification channels — how the system reaches the human.
 * Multiple can fire simultaneously (dashboard + system notification).
 */
export interface NotificationChannel {
  type: 'dashboard_banner' | 'system_notification' | 'terminal_prompt' | 'webhook';
  enabled: boolean;
  config: Record<string, unknown>;     // channel-specific (webhook URL, etc.)
}

/**
 * Dashboard integration: the approval queue lives as a panel in Witness.
 * Pending items show as a badge count + expandable drawer.
 */
export interface ApprovalQueueState {
  pending: PermissionRequest[];
  recentDecisions: Array<PermissionRequest & { decision: ApprovalDecision }>;
  stats: {
    totalToday: number;
    autoApproved: number;
    humanApproved: number;
    denied: number;
    avgResponseTimeMs: number;
  };
}


// ============================================================================
// 3. POLICY ENGINE (Auto-Approve Rules)
// ============================================================================

/**
 * The policy engine evaluates PermissionRequests against an ordered rule list.
 * First match wins (like iptables / nginx location blocks).
 * If no rule matches → falls back to risk-based default (high/critical = deny, else allow).
 */

export interface PolicyEngine {
  /** Evaluate a request against all rules. Returns decision or null (= needs human). */
  evaluate(request: PermissionRequest): PolicyEvalResult;

  /** Hot-reload rules without restart */
  loadRules(rules: PolicyRule[]): void;

  /** Get current rule set */
  getRules(): PolicyRule[];
}

export type PolicyEvalResult =
  | { outcome: 'auto_approve'; rule: PolicyRule; reason: string }
  | { outcome: 'auto_deny'; rule: PolicyRule; reason: string }
  | { outcome: 'needs_human'; reason: string };

/**
 * A single policy rule. Stored as YAML/JSON in ~/.witness/policies/
 * Human-editable, version-controlled.
 */
export interface PolicyRule {
  id: string;                          // slug: 'allow-git-push-feature-branches'
  name: string;                        // "Allow git push to feature branches"
  description: string;
  enabled: boolean;
  priority: number;                    // lower = evaluated first (0-999)
  createdAt: number;
  createdBy: string;                   // 'human:default' or 'system:learned'

  // Match conditions (ALL must be true)
  match: PolicyMatch;

  // Action when matched
  action: 'allow' | 'deny' | 'require_human';

  // Constraints on the allow
  constraints: PolicyConstraints | null;

  // Metadata
  hitCount: number;                    // how many times this rule fired
  lastHitAt: number | null;
}

/**
 * Matching criteria. All specified fields must match (AND logic).
 * Unspecified fields are wildcards.
 */
export interface PolicyMatch {
  /** Which agents this applies to */
  sources?: AgentSource[];
  /** Permission categories */
  categories?: PermissionCategory[];
  /** Risk levels this rule handles */
  riskLevels?: RiskLevel[];

  // Contextual matchers
  /** Glob patterns for workspace paths */
  workspacePaths?: string[];
  /** Git branch patterns (supports wildcards: 'feature/*') */
  gitBranches?: string[];
  /** Action string regex */
  actionPattern?: string;
  /** Tool names */
  toolNames?: string[];
  /** Time-of-day restrictions (e.g., only during work hours) */
  timeWindow?: TimeWindow | null;

  // Cost-based
  /** Max estimated cost in USD for auto-approve */
  maxCostUsd?: number;

  // Behavioral
  /** Only match if agent's total spend this session is below threshold */
  sessionBudgetRemainingAbove?: number;
  /** Only match if the run has had fewer than N denials */
  maxPriorDenials?: number;
}

export interface TimeWindow {
  /** Days of week (0=Sun, 6=Sat) */
  daysOfWeek: number[];
  /** Start hour (0-23, local time) */
  startHour: number;
  /** End hour (0-23, local time) */
  endHour: number;
  timezone: string;                    // IANA: 'America/New_York'
}

export interface PolicyConstraints {
  /** Max times this rule can fire per run */
  maxPerRun?: number;
  /** Max times this rule can fire per hour */
  maxPerHour?: number;
  /** Max cumulative cost this rule can approve per session */
  maxCumulativeCostUsd?: number;
  /** Require the workspace to be clean (no uncommitted changes) */
  requireCleanWorkspace?: boolean;
  /** Require specific branch patterns */
  requireBranch?: string[];
}

/**
 * Default policy set — ships with Witness, user can override.
 * Designed to be safe-by-default while not blocking normal development flow.
 */
export const DEFAULT_POLICIES: PolicyRule[] = [
  // --- Always allow ---
  {
    id: 'allow-reads',
    name: 'Allow all read operations',
    description: 'File reads, search, glob are always safe',
    enabled: true,
    priority: 0,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['read'] },
    action: 'allow',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
  {
    id: 'allow-writes-tracked',
    name: 'Allow file writes in git-tracked repos',
    description: 'Writes are reversible when git tracks the file',
    enabled: true,
    priority: 10,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['write'], gitBranches: ['*'] },
    action: 'allow',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
  {
    id: 'allow-shell-safe',
    name: 'Allow non-destructive shell commands',
    description: 'Shell commands that are read-only or build-related',
    enabled: true,
    priority: 20,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['shell'] },
    action: 'allow',
    constraints: { maxPerRun: 200 },
    hitCount: 0, lastHitAt: null,
  },
  {
    id: 'allow-push-feature',
    name: 'Allow git push to feature branches',
    description: 'Pushing to non-protected branches is low risk',
    enabled: true,
    priority: 30,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['git_push'], gitBranches: ['feature/*', 'fix/*', 'chore/*'] },
    action: 'allow',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
  // --- Always deny ---
  {
    id: 'deny-git-rewrite-main',
    name: 'Deny git history rewrite on main/master',
    description: 'Never allow force-push or rebase on protected branches',
    enabled: true,
    priority: 5,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['git_rewrite'], gitBranches: ['main', 'master', 'production'] },
    action: 'deny',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
  // --- Require human ---
  {
    id: 'human-push-protected',
    name: 'Require approval for push to main/master',
    description: 'Protected branch pushes need explicit human sign-off',
    enabled: true,
    priority: 25,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['git_push'], gitBranches: ['main', 'master', 'production'] },
    action: 'require_human',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
  {
    id: 'human-expensive-calls',
    name: 'Require approval for expensive API calls',
    description: 'Any single call estimated >$1 needs sign-off',
    enabled: true,
    priority: 40,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['network_cost'], maxCostUsd: 1.0 },
    action: 'allow',  // below threshold = allow
    constraints: { maxCumulativeCostUsd: 50.0 },
    hitCount: 0, lastHitAt: null,
  },
  {
    id: 'human-destructive-shell',
    name: 'Require approval for destructive shell commands',
    description: 'rm -rf, git clean, DROP, truncate, etc.',
    enabled: true,
    priority: 15,
    createdAt: 0, createdBy: 'system:default',
    match: { categories: ['shell_destroy'] },
    action: 'require_human',
    constraints: null,
    hitCount: 0, lastHitAt: null,
  },
] as PolicyRule[];


// ============================================================================
// 4. AUDIT TRAIL
// ============================================================================

/**
 * Every governance decision is logged to an append-only audit log.
 * Stored in ~/.witness/audit/ as JSONL files, rotated daily.
 * Never mutated after write — immutable ledger.
 */

export interface AuditEntry {
  id: string;                          // uuid v7
  timestamp: number;                   // epoch ms
  version: 1;                          // schema version for forward compat

  // What happened
  event: AuditEventType;

  // Request context (always present for approval events)
  request: PermissionRequest | null;

  // Decision (null for lifecycle events)
  decision: ApprovalDecision | null;

  // Policy evaluation trace (which rules were evaluated, in order)
  policyTrace: PolicyTraceEntry[] | null;

  // System state at time of decision
  systemContext: {
    activeRuns: number;
    pendingApprovals: number;
    sessionBudgetUsedUsd: number;
    uptimeMs: number;
  };
}

export type AuditEventType =
  | 'approval_requested'    // new request created
  | 'approval_granted'      // human or policy approved
  | 'approval_denied'       // human or policy denied
  | 'approval_expired'      // timed out
  | 'policy_rule_created'   // new rule added
  | 'policy_rule_modified'  // rule updated
  | 'policy_rule_deleted'   // rule removed
  | 'policy_reloaded'       // full policy set reloaded
  | 'session_started'       // witness process started
  | 'session_ended'         // witness process stopped
  | 'budget_exceeded'       // session budget threshold crossed
  | 'escalation';           // auto-escalated to stricter approval

export interface PolicyTraceEntry {
  ruleId: string;
  ruleName: string;
  matched: boolean;
  /** Which match condition failed (null if all matched) */
  failedCondition: string | null;
}

/**
 * Audit query interface — for the dashboard's audit log viewer.
 */
export interface AuditStore {
  /** Append a new entry (write-only, never mutate) */
  append(entry: AuditEntry): void;

  /** Query entries with filters */
  query(filter: AuditFilter): AuditEntry[];

  /** Get summary stats for a time range */
  summarize(since: number, until: number): AuditSummary;
}

export interface AuditFilter {
  since?: number;
  until?: number;
  events?: AuditEventType[];
  sources?: AgentSource[];
  runIds?: string[];
  onlyDenied?: boolean;
  onlyHuman?: boolean;
  limit?: number;
  offset?: number;
}

export interface AuditSummary {
  period: { since: number; until: number };
  totalRequests: number;
  byOutcome: Record<'approved' | 'denied' | 'expired', number>;
  bySource: Record<string, number>;
  byCategory: Record<PermissionCategory, number>;
  topDeniedActions: Array<{ action: string; count: number }>;
  avgHumanResponseMs: number;
  autoApproveRate: number;             // 0-1
}


// ============================================================================
// 5. AGENT INTEGRATION — Per-Agent Permission Model Bridges
// ============================================================================

/**
 * Each agent has its own native permission/approval mechanism.
 * The governance layer integrates via agent-specific bridges that:
 * 1. Intercept permission checks before they reach the user's terminal
 * 2. Translate native requests into PermissionRequest
 * 3. Feed decisions back to the agent's native approval channel
 *
 * Integration modes (from least to most invasive):
 * - OBSERVE: read-only, log decisions the agent makes natively (all agents)
 * - GATE:    intercept + approve/deny before the agent acts (requires agent support)
 * - INJECT:  modify agent's permission config at startup (pre-configure)
 */

export type IntegrationMode = 'observe' | 'gate' | 'inject';

/**
 * Base interface for all agent bridges.
 */
export interface AgentGovernanceBridge {
  readonly source: AgentSource;
  readonly supportedModes: IntegrationMode[];
  readonly activeMode: IntegrationMode;

  /** Classify a tool call into a PermissionCategory + RiskLevel */
  classifyAction(toolName: string, toolInput: unknown): ActionClassification;

  /** Start intercepting/observing this agent's permission requests */
  activate(): Promise<void>;

  /** Stop intercepting, restore native behavior */
  deactivate(): Promise<void>;
}

export interface ActionClassification {
  category: PermissionCategory;
  risk: RiskLevel;
  action: string;             // normalized action string
  affectedPaths: string[];
  estimatedCost: CostEstimate | null;
  /** Destructive patterns detected in command */
  destructivePatterns: string[];
}

// ---------------------------------------------------------------------------
// Claude Code Bridge
// ---------------------------------------------------------------------------

/**
 * Claude Code has a built-in permission system with 4 modes:
 * - default: asks for shell/write approval via stdin
 * - plan: blocks all tool execution, only plans
 * - auto: auto-approves known-safe tools (Read, Search, Glob, etc.)
 * - yolo: approves everything (DANGEROUS)
 *
 * Integration approach:
 * - OBSERVE mode: parse 'permission-mode' events from jsonl, log all tool calls
 * - GATE mode: use MCP permission hooks (if available) or wrap via custom MCP server
 * - INJECT mode: set permission mode to 'default' and provide answers via stdin pipe
 *
 * Claude Code exposes its permission state in jsonl as:
 * {"type": "permission-mode", "permissionMode": "default"}
 * {"type": "system", "system": "permission_granted", ...}
 */
export interface ClaudeCodeBridge extends AgentGovernanceBridge {
  source: 'claude-code';
  supportedModes: ['observe', 'gate', 'inject'];

  /** Current permission mode the Claude Code instance is running in */
  nativeMode: 'default' | 'plan' | 'auto' | 'yolo' | null;

  /** Tools that Claude Code auto-approves in its native 'auto' mode */
  nativeAutoApproved: Set<string>;  // Read, Search, Glob, etc.

  /**
   * Destructive command patterns Claude Code checks internally.
   * We mirror these for consistent classification.
   */
  destructivePatterns: RegExp[];

  /**
   * In GATE mode: listen for permission prompts via the jsonl stream
   * and inject approval/denial before the user sees the terminal prompt.
   */
  interceptPermissionPrompt?(runId: string): AsyncIterable<PermissionRequest>;
}

// ---------------------------------------------------------------------------
// Codex Bridge
// ---------------------------------------------------------------------------

/**
 * Codex (OpenAI CLI) permission model:
 * - Sandboxed by default: runs in a container/namespace, limited FS access
 * - "Full auto" mode: auto-approves within sandbox boundaries
 * - Network access: disabled by default, requires explicit --net flag
 *
 * Integration approach:
 * - OBSERVE mode: parse tool_calls from streaming jsonl, classify each
 * - INJECT mode: configure sandbox policy via codex config before launch
 * - GATE mode: not natively supported (Codex doesn't expose approval hooks)
 *
 * Key difference: Codex's sandbox means most shell ops are already contained.
 * Governance focus is on: network access, git push (escapes sandbox), cost.
 */
export interface CodexBridge extends AgentGovernanceBridge {
  source: 'codex';
  supportedModes: ['observe', 'inject'];

  /** Whether the Codex instance is sandboxed */
  sandboxed: boolean;
  /** Whether network access was granted */
  networkEnabled: boolean;

  /** Codex's internal approval mode */
  nativeMode: 'suggest' | 'auto-edit' | 'full-auto';

  /**
   * Codex doesn't expose real-time approval hooks.
   * Instead, we classify after-the-fact from the streaming jsonl.
   * For true gating, must use INJECT mode with restricted config.
   */
  classifyFromStream(event: { type: string; content: unknown }): ActionClassification | null;
}

// ---------------------------------------------------------------------------
// Hermes Bridge
// ---------------------------------------------------------------------------

/**
 * Hermes is a proxy/orchestrator that routes to Claude/Codex/other LLMs.
 * It has its own permission model based on:
 * - Tool allowlists per session
 * - Budget caps (token + dollar)
 * - Continuation approval (multi-step workflows)
 *
 * Integration approach:
 * - OBSERVE mode: query state.db for tool_calls, classify
 * - GATE mode: Hermes supports plugin hooks — register as approval middleware
 * - INJECT mode: configure tool allowlist + budget before session start
 *
 * Key: Hermes already has budget controls. Governance layer augments with:
 * - Cross-agent budget tracking (Hermes doesn't know about Codex spend)
 * - Destructive op detection (Hermes treats all tools equally)
 */
export interface HermesBridge extends AgentGovernanceBridge {
  source: 'hermes';
  supportedModes: ['observe', 'gate', 'inject'];

  /** Hermes session budget (tokens) */
  sessionBudget: { maxTokens: number; usedTokens: number } | null;
  /** Hermes tool allowlist for this session */
  allowedTools: string[] | null;

  /**
   * In GATE mode: register as Hermes middleware via plugin API.
   * Hermes calls our hook before executing any tool.
   */
  registerMiddleware?(config: { hermesDbPath: string }): Promise<void>;
}

// ---------------------------------------------------------------------------
// OpenCode Bridge
// ---------------------------------------------------------------------------

/**
 * OpenCode stores sessions in SQLite with Drizzle ORM.
 * Permission model:
 * - Auto-approves most operations (designed for autonomous coding)
 * - User can interrupt via terminal
 * - No built-in destructive op detection
 *
 * Integration approach:
 * - OBSERVE mode: tail opencode.db for new tool calls, classify
 * - GATE mode: not supported (OpenCode has no approval hooks)
 * - INJECT mode: limited — can set session config before launch
 *
 * Governance value: OpenCode is the most "yolo" of the agents.
 * Adding a governance layer provides safety net for unattended runs.
 */
export interface OpenCodeBridge extends AgentGovernanceBridge {
  source: 'opencode';
  supportedModes: ['observe'];

  /** OpenCode doesn't support gating — observe-only with alerting */
  alertOnDestructive: boolean;

  /**
   * Best we can do: detect destructive ops from DB and alert human.
   * Cannot block execution (already happened by the time we see it in DB).
   */
  detectAndAlert(toolCall: { name: string; input: unknown }): {
    destructive: boolean;
    alert: string | null;
  };
}

// ---------------------------------------------------------------------------
// OMP (Oh My Pi) Bridge
// ---------------------------------------------------------------------------

/**
 * OMP (oh-my-pi / pi-coding-agent) permission model:
 * - Similar to Claude Code (terminal-based approval)
 * - Supports permission profiles (conservative / balanced / aggressive)
 * - Logs to ~/.omp/agent/sessions/ as jsonl
 *
 * Integration approach:
 * - OBSERVE mode: parse session jsonl for tool calls
 * - GATE mode: OMP supports approval hooks via its plugin system
 * - INJECT mode: set permission profile at launch
 */
export interface OmpBridge extends AgentGovernanceBridge {
  source: 'omp';
  supportedModes: ['observe', 'gate', 'inject'];

  /** OMP's native permission profile */
  nativeProfile: 'conservative' | 'balanced' | 'aggressive' | null;

  /**
   * In GATE mode: OMP exposes a hook file that the governance layer
   * writes approval decisions to. OMP polls this file before executing.
   */
  approvalHookPath: string | null;
}


// ============================================================================
// 6. DESTRUCTIVE COMMAND DETECTION
// ============================================================================

/**
 * Shared command classifier used by all bridges.
 * Detects destructive patterns in shell commands.
 */
export interface DestructiveCommandClassifier {
  /** Classify a shell command string */
  classify(command: string): {
    destructive: boolean;
    category: PermissionCategory;
    patterns: string[];         // which patterns matched
    confidence: number;        // 0-1, how certain we are
  };
}

/**
 * Pattern registry — extensible set of destructive command patterns.
 * Users can add custom patterns via ~/.witness/destructive-patterns.yaml
 */
export const DESTRUCTIVE_PATTERNS: Array<{
  pattern: RegExp;
  category: PermissionCategory;
  description: string;
}> = [
  // File system destruction
  { pattern: /\brm\s+(-[a-z]*f|-[a-z]*r|--force|--recursive)\b/i, category: 'shell_destroy', description: 'rm with force/recursive' },
  { pattern: /\brm\s+-rf\b|\brm\s+-fr\b/i, category: 'shell_destroy', description: 'rm -rf' },
  { pattern: /\brmdir\b/i, category: 'shell_destroy', description: 'rmdir' },
  { pattern: />\s*\/dev\/null\s*2>&1.*&&\s*rm/i, category: 'shell_destroy', description: 'silent delete' },
  { pattern: /\bshred\b|\bwipe\b/i, category: 'shell_destroy', description: 'secure delete' },

  // Git destructive
  { pattern: /\bgit\s+push\b/i, category: 'git_push', description: 'git push' },
  { pattern: /\bgit\s+push\s+.*--force\b|\bgit\s+push\s+-f\b/i, category: 'git_rewrite', description: 'force push' },
  { pattern: /\bgit\s+reset\s+--hard\b/i, category: 'git_rewrite', description: 'git reset --hard' },
  { pattern: /\bgit\s+rebase\b/i, category: 'git_rewrite', description: 'git rebase' },
  { pattern: /\bgit\s+clean\s+-[a-z]*f/i, category: 'shell_destroy', description: 'git clean -f' },
  { pattern: /\bgit\s+filter-branch\b/i, category: 'git_rewrite', description: 'git filter-branch' },

  // Database destructive
  { pattern: /\bDROP\s+(TABLE|DATABASE|INDEX)\b/i, category: 'shell_destroy', description: 'SQL DROP' },
  { pattern: /\bTRUNCATE\b/i, category: 'shell_destroy', description: 'SQL TRUNCATE' },
  { pattern: /\bDELETE\s+FROM\b(?!.*\bWHERE\b)/i, category: 'shell_destroy', description: 'DELETE without WHERE' },

  // System modification
  { pattern: /\bsudo\b/i, category: 'system', description: 'sudo' },
  { pattern: /\bcurl\b.*\|\s*(sudo\s+)?(ba)?sh\b/i, category: 'system', description: 'pipe to shell' },
  { pattern: /\bnpm\s+(i|install)\s+-g\b|\bpip\s+install\b/i, category: 'system', description: 'global package install' },
  { pattern: /\bchmod\s+[0-7]*7[0-7]*\b|\bchmod\s+.*\+[xs]/i, category: 'system', description: 'permission change' },

  // Network/cost
  { pattern: /\bcurl\b|\bwget\b|\bfetch\b/i, category: 'network', description: 'network request' },
  { pattern: /\bdocker\s+(rm|rmi|prune|system\s+prune)\b/i, category: 'shell_destroy', description: 'docker cleanup' },
];


// ============================================================================
// 7. GOVERNANCE SERVICE — Top-Level Orchestrator
// ============================================================================

/**
 * The GovernanceService is the top-level coordinator.
 * It wires together: bridges → classifier → policy engine → approval queue → audit.
 *
 * Lifecycle:
 * 1. Agent makes a tool call
 * 2. Bridge intercepts (GATE mode) or observes (OBSERVE mode)
 * 3. Bridge calls classifyAction → ActionClassification
 * 4. Service creates PermissionRequest
 * 5. PolicyEngine evaluates → auto-approve/deny/needs-human
 * 6. If needs-human → push to ApprovalQueue, notify via channels
 * 7. Human responds → decision flows back to bridge → agent proceeds
 * 8. AuditStore logs everything
 */
export interface GovernanceService {
  /** Initialize with config, load policies, activate bridges */
  start(config: GovernanceConfig): Promise<void>;

  /** Gracefully shut down, flush audit log */
  stop(): Promise<void>;

  /** Submit a new permission request (called by bridges) */
  submitRequest(request: Omit<PermissionRequest, 'id' | 'status' | 'decision'>): Promise<ApprovalDecision>;

  /** Human responds to a pending request (called by UI) */
  resolveRequest(response: ApprovalResponse): Promise<void>;

  /** Get current state for dashboard */
  getState(): ApprovalQueueState;

  /** Subscribe to state changes (for real-time dashboard updates) */
  subscribe(handler: (state: ApprovalQueueState) => void): () => void;
}

export interface GovernanceConfig {
  /** Where to store policies and audit logs */
  dataDir: string;                     // default: ~/.witness/governance/

  /** Active bridges */
  bridges: AgentGovernanceBridge[];

  /** Notification channels */
  notifications: NotificationChannel[];

  /** Global session budget (all agents combined) */
  sessionBudget: {
    maxUsd: number;                    // e.g., 100.0
    warningThresholdPct: number;       // e.g., 0.8 = warn at 80%
  };

  /** How long a pending request waits before auto-denying */
  approvalTimeoutMs: number;           // default: 300_000 (5 minutes)

  /** How long approval grants last (for forRun/forSession scopes) */
  grantTtlMs: number;                  // default: 3_600_000 (1 hour)
}


// ============================================================================
// 8. DASHBOARD INTEGRATION — AgentEvent Extension
// ============================================================================

/**
 * Governance events are emitted into the same AgentEvent stream
 * that the Witness dashboard already consumes. This means:
 * - Approval requests show in the event timeline
 * - Denied actions are visible as "blocked" steps
 * - The topology graph shows governance decisions as annotations
 */

export interface GovernanceEvent extends BaseEvent {
  kind: 'governance';
  data: {
    type: 'approval_requested' | 'approval_granted' | 'approval_denied' | 'budget_warning';
    requestId: string;
    category: PermissionCategory;
    risk: RiskLevel;
    action: string;
    decidedBy: DecisionSource | null;
    blockedDurationMs: number | null;  // how long the agent waited
  };
}

// Re-export for convenience
import type { AgentSource } from './01-data-model';
interface BaseEvent {
  eventId: string;
  runId: string;
  turnId: string | null;
  timestamp: number;
  source: AgentSource;
  monotonicSeq: number;
}
