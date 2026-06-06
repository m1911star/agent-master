// ============================================================
// Workflow Templates & Triggers — TypeScript Interfaces + YAML
// Template system for reusable workflow definitions
// ============================================================

// ─── Template Parameter System ──────────────────────────────

/** Supported parameter types for template slots */
export type ParameterType =
  | 'string'
  | 'number'
  | 'boolean'
  | 'string[]'
  | 'enum'
  | 'duration'    // e.g., '30s', '5m', '1h'
  | 'agent-ref'   // reference to an agent by source+id
  | 'template-ref'; // reference to another template

/** Validation rule for a parameter */
export interface ParameterValidation {
  /** Regex pattern for string types */
  pattern?: string;
  /** Min value for numbers, min length for strings/arrays */
  min?: number;
  /** Max value for numbers, max length for strings/arrays */
  max?: number;
  /** Allowed values for enum type */
  enumValues?: string[];
  /** Custom validation error message */
  message?: string;
}

/**
 * A typed parameter that a template exposes for customization.
 */
export interface TemplateParameter {
  /** Parameter name (used in expressions as `${{params.name}}`) */
  name: string;
  /** Human-readable description */
  description: string;
  /** Parameter data type */
  type: ParameterType;
  /** Whether the parameter must be provided */
  required: boolean;
  /** Default value (must match type) */
  default?: string | number | boolean | string[];
  /** Validation constraints */
  validation?: ParameterValidation;
  /** Example values for documentation */
  examples?: string[];
}

// ─── Template Slots ─────────────────────────────────────────

/** Where a slot can be injected relative to a step */
export type SlotPosition = 'before' | 'after' | 'replace' | 'wrap';

/**
 * Named extension point where users inject custom steps.
 * Slots allow templates to be customized without forking.
 */
export interface TemplateSlot {
  /** Unique slot name within the template */
  name: string;
  /** Human-readable description of what goes here */
  description: string;
  /** Position relative to the anchor step */
  position: SlotPosition;
  /** Step ID this slot is anchored to */
  anchorStepId: string;
  /** Whether the slot must be filled when instantiating */
  required: boolean;
  /** Maximum number of steps that can be injected */
  maxSteps?: number;
  /** Constraints on what step types can fill this slot */
  allowedStepTypes?: string[];
}

// ─── Template Composition ───────────────────────────────────

/** How a template references and incorporates another template */
export interface TemplateInclude {
  /** ID of the template to include */
  templateId: string;
  /** Version constraint (semver range) */
  version?: string;
  /** Parameter bindings passed to the included template */
  parameterBindings: Record<string, string | number | boolean>;
  /** Step ID in parent where included template is inserted */
  insertAt: string;
  /** Slot fills to apply to the included template */
  slotFills?: Record<string, TemplateStepDefinition[]>;
}

/** How a template extends (inherits from) a base template */
export interface TemplateExtension {
  /** Base template ID */
  baseTemplateId: string;
  /** Base template version constraint */
  baseVersion?: string;
  /** Steps to override (by step ID) */
  overrides: Record<string, Partial<TemplateStepDefinition>>;
  /** Steps to remove from base */
  removals: string[];
  /** Additional steps to insert */
  additions: Array<{
    step: TemplateStepDefinition;
    after?: string;
    before?: string;
  }>;
}

/** Composition strategies for building templates from parts */
export interface TemplateComposition {
  /** Templates included inline */
  includes: TemplateInclude[];
  /** Base template being extended (single inheritance) */
  extends?: TemplateExtension;
}

// ─── Trigger System ─────────────────────────────────────────

/** Event-based trigger: fires when a matching agent event occurs */
export interface EventTrigger {
  type: 'event';
  /** Event type to match (e.g., 'run.completed', 'step.failed') */
  eventType: string;
  /** Filter conditions on event payload */
  filters: Record<string, string | number | boolean>;
  /** Optional source filter */
  source?: string;
}

/** Schedule-based trigger: fires on a cron schedule */
export interface ScheduleTrigger {
  type: 'schedule';
  /** Cron expression (5 or 6 fields) */
  cron: string;
  /** Timezone for cron evaluation */
  timezone?: string;
}

/** Condition-based trigger: fires when a condition becomes true */
export interface ConditionalTrigger {
  type: 'conditional';
  /** Expression to evaluate (e.g., "run.metrics.errorCount > 5") */
  condition: string;
  /** How often to check the condition (ms) */
  pollIntervalMs: number;
  /** Only fire once per condition-true period */
  deduplicate: boolean;
}

/** Manual trigger: user-initiated with optional required inputs */
export interface ManualTrigger {
  type: 'manual';
  /** Label for the trigger button in UI */
  buttonLabel: string;
  /** Parameters the user must provide when triggering */
  requiredInputs: string[];
  /** Confirmation message before triggering */
  confirmMessage?: string;
}

/** Composite trigger: logical combination of other triggers */
export interface CompositeTrigger {
  type: 'composite';
  /** Logical operator */
  operator: 'AND' | 'OR';
  /** Child triggers */
  triggers: TriggerDefinition[];
  /** For AND: all must fire within this window (ms) */
  windowMs?: number;
}

export type TriggerDefinition =
  | EventTrigger
  | ScheduleTrigger
  | ConditionalTrigger
  | ManualTrigger
  | CompositeTrigger;

/** Debounce/throttle configuration for triggers */
export interface TriggerRateLimit {
  /** Minimum time between trigger fires (ms) */
  debounceMs?: number;
  /** Maximum fires per time window */
  maxFires?: number;
  /** Time window for maxFires (ms) */
  windowMs?: number;
}

/** Complete trigger configuration attached to a template */
export interface TriggerConfig {
  trigger: TriggerDefinition;
  /** Rate limiting */
  rateLimit?: TriggerRateLimit;
  /** Whether the trigger is currently enabled */
  enabled: boolean;
  /** Priority when multiple triggers fire simultaneously */
  priority: number;
}

// ─── Template Step Definition ───────────────────────────────

export type StepType =
  | 'agent-task'     // Delegate to an agent
  | 'gate'           // Wait for approval
  | 'condition'      // Branch based on expression
  | 'parallel'       // Run children in parallel
  | 'template-ref'   // Inline another template
  | 'webhook'        // Call external service
  | 'delay';         // Wait for duration

export interface TemplateStepDefinition {
  /** Unique step ID within the template */
  id: string;
  /** Human-readable name */
  name: string;
  /** Step type */
  type: StepType;
  /** Dependencies (step IDs that must complete before this runs) */
  dependsOn: string[];
  /** Condition expression for whether to run this step */
  when?: string;
  /** Step-specific configuration */
  config: Record<string, unknown>;
  /** Retry policy */
  retry?: {
    maxAttempts: number;
    backoffMs: number;
    backoffMultiplier: number;
  };
  /** Timeout for this step */
  timeoutMs?: number;
  /** Error handling strategy */
  onFailure?: 'fail' | 'skip' | 'continue' | 'retry';
}

// ─── Template Registry & Discovery ──────────────────────────

/** Template metadata for registry listing */
export interface TemplateRegistryEntry {
  id: string;
  name: string;
  description: string;
  version: string;
  author: string;
  tags: string[];
  /** When this template was last modified */
  updatedAt: number;
  /** Number of times instantiated */
  useCount: number;
  /** Average duration from historical runs */
  avgDurationMs?: number;
  /** Source: built-in, user-defined, or discovered from agent behavior */
  origin: 'builtin' | 'user' | 'discovered';
}

/** How agents discover available templates */
export interface TemplateDiscovery {
  /** Search templates by tag, name, or description */
  search(query: string, tags?: string[]): TemplateRegistryEntry[];
  /** Get a specific template by ID and version */
  get(id: string, version?: string): WorkflowTemplate | null;
  /** List all templates matching filters */
  list(filters?: {
    origin?: 'builtin' | 'user' | 'discovered';
    tags?: string[];
    minVersion?: string;
  }): TemplateRegistryEntry[];
  /** Instantiate a template with parameters, returning a workflow instance */
  instantiate(
    templateId: string,
    parameters: Record<string, unknown>,
    slotFills?: Record<string, TemplateStepDefinition[]>,
  ): WorkflowInstance;
}

/** A workflow instance created from a template */
export interface WorkflowInstance {
  /** Unique instance ID */
  id: string;
  /** Source template ID */
  templateId: string;
  /** Template version used */
  templateVersion: string;
  /** Resolved parameters */
  parameters: Record<string, unknown>;
  /** Resolved steps (all includes expanded, slots filled) */
  steps: TemplateStepDefinition[];
  /** When instantiated */
  createdAt: number;
  /** Current status */
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
}

// ─── Top-Level Template Interface ───────────────────────────

/**
 * Complete workflow template definition.
 * Templates are the reusable blueprints from which workflow instances are created.
 */
export interface WorkflowTemplate {
  /** Unique template identifier */
  id: string;
  /** Human-readable name */
  name: string;
  /** Description of what this workflow does */
  description: string;
  /** Semantic version */
  version: string;
  /** Template author */
  author: string;
  /** Categorization tags */
  tags: string[];

  /** Configurable parameters */
  parameters: TemplateParameter[];
  /** Extension points */
  slots: TemplateSlot[];
  /** Ordered step definitions (DAG via dependsOn) */
  steps: TemplateStepDefinition[];
  /** Trigger configurations */
  triggers: TriggerConfig[];
  /** Composition (includes/extends) */
  composition?: TemplateComposition;

  /** Template-level defaults */
  defaults: {
    timeoutMs: number;
    retryPolicy: {
      maxAttempts: number;
      backoffMs: number;
      backoffMultiplier: number;
    };
    onFailure: 'fail' | 'skip' | 'continue';
  };
}

// ─── YAML Template Examples ─────────────────────────────────

/**
 * Deploy pipeline template: build → test → stage → canary → promote
 */
export const TEMPLATE_DEPLOY_YAML = `\
id: deploy-pipeline
name: Deploy Pipeline
description: Multi-stage deployment with canary analysis and rollback
version: "1.0.0"
author: witness-system
tags: [deploy, ci-cd, canary]

parameters:
  - name: service_name
    description: Name of the service to deploy
    type: string
    required: true
    validation:
      pattern: "^[a-z][a-z0-9-]*$"
      message: "Service name must be lowercase alphanumeric with hyphens"

  - name: target_env
    description: Target deployment environment
    type: enum
    required: true
    default: staging
    validation:
      enumValues: [staging, production]

  - name: canary_percentage
    description: Initial canary traffic percentage
    type: number
    required: false
    default: 5
    validation:
      min: 1
      max: 50

  - name: rollback_on_error_rate
    description: Error rate threshold (%) to trigger rollback
    type: number
    required: false
    default: 2
    validation:
      min: 0.1
      max: 100

slots:
  - name: post-build-checks
    description: Custom checks to run after build (e.g., security scan)
    position: after
    anchorStepId: build
    required: false
    maxSteps: 5
    allowedStepTypes: [agent-task, webhook]

  - name: pre-promote-gate
    description: Custom approval gate before full promotion
    position: before
    anchorStepId: promote
    required: false
    allowedStepTypes: [gate, condition]

steps:
  - id: build
    name: Build \${{params.service_name}}
    type: agent-task
    dependsOn: []
    config:
      agent_source: claude-code
      prompt: |
        Build the service \${{params.service_name}} for \${{params.target_env}}.
        Run the build script and report any errors.
      timeout_ms: 300000
    onFailure: fail

  - id: unit-test
    name: Run Unit Tests
    type: agent-task
    dependsOn: [build]
    config:
      agent_source: claude-code
      prompt: "Run unit tests for \${{params.service_name}}"
    retry:
      maxAttempts: 2
      backoffMs: 5000
      backoffMultiplier: 2

  - id: integration-test
    name: Run Integration Tests
    type: agent-task
    dependsOn: [build]
    config:
      agent_source: claude-code
      prompt: "Run integration tests for \${{params.service_name}}"
    retry:
      maxAttempts: 2
      backoffMs: 10000
      backoffMultiplier: 2

  - id: stage-deploy
    name: Deploy to Staging
    type: agent-task
    dependsOn: [unit-test, integration-test]
    when: "params.target_env == 'production'"
    config:
      agent_source: claude-code
      prompt: "Deploy \${{params.service_name}} to staging environment"

  - id: canary-deploy
    name: Canary Deploy (\${{params.canary_percentage}}%)
    type: agent-task
    dependsOn: [stage-deploy]
    when: "params.target_env == 'production'"
    config:
      agent_source: claude-code
      prompt: |
        Deploy \${{params.service_name}} as canary with
        \${{params.canary_percentage}}% traffic

  - id: canary-analysis
    name: Analyze Canary Metrics
    type: condition
    dependsOn: [canary-deploy]
    config:
      expression: "metrics.error_rate < params.rollback_on_error_rate"
      on_true: promote
      on_false: rollback
    timeoutMs: 600000

  - id: promote
    name: Promote to Full Traffic
    type: agent-task
    dependsOn: [canary-analysis]
    config:
      agent_source: claude-code
      prompt: "Promote \${{params.service_name}} canary to 100% traffic"

  - id: rollback
    name: Rollback Canary
    type: agent-task
    dependsOn: [canary-analysis]
    when: "steps.canary-analysis.result == 'false'"
    config:
      agent_source: claude-code
      prompt: "Rollback \${{params.service_name}} canary deployment"
    onFailure: fail

triggers:
  - trigger:
      type: event
      eventType: run.completed
      filters:
        source: claude-code
        "metadata.intent": deploy
    enabled: true
    priority: 1
    rateLimit:
      debounceMs: 60000

  - trigger:
      type: manual
      buttonLabel: Deploy Now
      requiredInputs: [service_name, target_env]
      confirmMessage: "Deploy \${{params.service_name}} to \${{params.target_env}}?"
    enabled: true
    priority: 0

defaults:
  timeoutMs: 600000
  retryPolicy:
    maxAttempts: 3
    backoffMs: 5000
    backoffMultiplier: 2
  onFailure: fail
`;

/**
 * Code review workflow: lint → test → parallel review agents → approve
 */
export const TEMPLATE_CODE_REVIEW_YAML = `\
id: code-review
name: Automated Code Review
description: Multi-agent code review with lint, test, and parallel reviewer perspectives
version: "1.0.0"
author: witness-system
tags: [review, quality, agents]

parameters:
  - name: pr_ref
    description: Pull request reference (branch or PR number)
    type: string
    required: true
    examples: ["feature/auth-flow", "#142"]

  - name: review_depth
    description: How thorough the review should be
    type: enum
    required: false
    default: standard
    validation:
      enumValues: [quick, standard, thorough]

  - name: reviewers
    description: Agent reviewer perspectives to use
    type: string[]
    required: false
    default: [correctness, security, performance]

  - name: require_all_pass
    description: Whether all reviewers must approve
    type: boolean
    required: false
    default: false

slots:
  - name: custom-lint-rules
    description: Additional lint steps (e.g., project-specific rules)
    position: after
    anchorStepId: lint
    required: false
    allowedStepTypes: [agent-task, webhook]

  - name: post-review-action
    description: Actions to take after review completes (e.g., auto-merge)
    position: after
    anchorStepId: approve-gate
    required: false

steps:
  - id: lint
    name: Lint & Format Check
    type: agent-task
    dependsOn: []
    config:
      agent_source: claude-code
      prompt: |
        Check out \${{params.pr_ref}} and run linting.
        Report any lint errors or formatting issues.
        Output structured list of issues with file:line references.

  - id: typecheck
    name: Type Check
    type: agent-task
    dependsOn: []
    config:
      agent_source: claude-code
      prompt: "Run type checking on \${{params.pr_ref}}, report any type errors"

  - id: test
    name: Run Tests
    type: agent-task
    dependsOn: [lint, typecheck]
    config:
      agent_source: claude-code
      prompt: "Run the test suite for changes in \${{params.pr_ref}}"
    retry:
      maxAttempts: 2
      backoffMs: 5000
      backoffMultiplier: 1

  - id: review-agents
    name: Parallel Agent Reviews
    type: parallel
    dependsOn: [test]
    config:
      branches:
        - id: review-correctness
          name: Correctness Review
          when: "'correctness' in params.reviewers"
          config:
            agent_source: claude-code
            prompt: |
              Review \${{params.pr_ref}} for correctness.
              Depth: \${{params.review_depth}}.
              Focus: logic errors, edge cases, missing validation,
              incorrect assumptions, race conditions.

        - id: review-security
          name: Security Review
          when: "'security' in params.reviewers"
          config:
            agent_source: claude-code
            prompt: |
              Review \${{params.pr_ref}} for security issues.
              Depth: \${{params.review_depth}}.
              Focus: injection, auth bypass, data exposure,
              dependency vulnerabilities, secrets in code.

        - id: review-performance
          name: Performance Review
          when: "'performance' in params.reviewers"
          config:
            agent_source: claude-code
            prompt: |
              Review \${{params.pr_ref}} for performance.
              Depth: \${{params.review_depth}}.
              Focus: N+1 queries, unnecessary allocations,
              missing indexes, blocking operations, memory leaks.

  - id: synthesize
    name: Synthesize Reviews
    type: agent-task
    dependsOn: [review-agents]
    config:
      agent_source: claude-code
      prompt: |
        Synthesize the review results from all reviewers.
        Produce a unified review summary with severity ratings.
        Determine: approve, request-changes, or block.

  - id: approve-gate
    name: Approval Gate
    type: gate
    dependsOn: [synthesize]
    config:
      condition: "steps.synthesize.output.decision == 'approve'"
      auto_approve: true
      notify: [author]
      timeout_ms: 86400000

triggers:
  - trigger:
      type: event
      eventType: run.completed
      filters:
        source: claude-code
        "metadata.has_code_changes": true
    enabled: true
    priority: 1
    rateLimit:
      debounceMs: 30000

  - trigger:
      type: composite
      operator: AND
      triggers:
        - type: event
          eventType: step.completed
          filters:
            toolKind: git
            "metadata.action": push
        - type: conditional
          condition: "event.metadata.branch != 'main'"
          pollIntervalMs: 0
          deduplicate: true
      windowMs: 5000
    enabled: true
    priority: 2

defaults:
  timeoutMs: 300000
  retryPolicy:
    maxAttempts: 2
    backoffMs: 3000
    backoffMultiplier: 2
  onFailure: continue
`;

/**
 * Debug-and-fix workflow: reproduce → diagnose → fix → verify
 */
export const TEMPLATE_DEBUG_FIX_YAML = `\
id: debug-and-fix
name: Debug & Fix
description: Systematic debugging workflow - reproduce, diagnose, fix, and verify
version: "1.0.0"
author: witness-system
tags: [debug, fix, systematic]

parameters:
  - name: issue_description
    description: Description of the bug or issue to debug
    type: string
    required: true

  - name: reproduction_steps
    description: Known steps to reproduce (if available)
    type: string
    required: false

  - name: affected_files
    description: Files known to be involved
    type: string[]
    required: false
    default: []

  - name: max_fix_attempts
    description: Maximum number of fix attempts before escalating
    type: number
    required: false
    default: 3
    validation:
      min: 1
      max: 10

  - name: auto_apply_fix
    description: Automatically apply the fix without human approval
    type: boolean
    required: false
    default: false

slots:
  - name: additional-diagnostics
    description: Custom diagnostic steps (e.g., log analysis, profiling)
    position: after
    anchorStepId: diagnose
    required: false
    allowedStepTypes: [agent-task, webhook]

  - name: pre-fix-approval
    description: Gate before applying fix (e.g., senior review)
    position: before
    anchorStepId: fix
    required: false
    allowedStepTypes: [gate]

steps:
  - id: reproduce
    name: Reproduce Issue
    type: agent-task
    dependsOn: []
    config:
      agent_source: claude-code
      prompt: |
        Reproduce the following issue:
        \${{params.issue_description}}

        Known reproduction steps: \${{params.reproduction_steps || 'None provided'}}
        Affected files: \${{params.affected_files}}

        Create a minimal reproduction. If you cannot reproduce,
        document what you tried and what happened instead.
      timeout_ms: 180000
    onFailure: continue

  - id: diagnose
    name: Root Cause Analysis
    type: agent-task
    dependsOn: [reproduce]
    config:
      agent_source: claude-code
      prompt: |
        Based on the reproduction results, perform root cause analysis.
        Examine: stack traces, state at failure point, recent changes,
        related code paths.
        Output: root cause hypothesis, confidence level, affected scope.
      timeout_ms: 300000

  - id: fix-strategy
    name: Determine Fix Strategy
    type: condition
    dependsOn: [diagnose]
    config:
      expression: "steps.diagnose.output.confidence > 0.7"
      on_true: fix
      on_false: gather-more-context

  - id: gather-more-context
    name: Gather Additional Context
    type: agent-task
    dependsOn: [fix-strategy]
    when: "steps.diagnose.output.confidence <= 0.7"
    config:
      agent_source: claude-code
      prompt: |
        The diagnosis confidence is low. Gather more context:
        - Check git blame for recent changes
        - Search for similar issues
        - Examine test coverage gaps
        - Check related subsystems

  - id: fix
    name: Implement Fix
    type: agent-task
    dependsOn: [fix-strategy, gather-more-context]
    config:
      agent_source: claude-code
      prompt: |
        Implement a fix for the diagnosed root cause.
        Root cause: \${{steps.diagnose.output.root_cause}}
        Strategy: minimal, targeted fix that addresses the root cause
        without side effects. Include inline comments explaining why.
      timeout_ms: 300000
    retry:
      maxAttempts: "\${{params.max_fix_attempts}}"
      backoffMs: 10000
      backoffMultiplier: 1.5

  - id: verify-fix
    name: Verify Fix
    type: agent-task
    dependsOn: [fix]
    config:
      agent_source: claude-code
      prompt: |
        Verify the fix:
        1. Run the reproduction steps — issue should not occur
        2. Run existing tests — no regressions
        3. Add a regression test for this specific case
        4. Check edge cases around the fix
      timeout_ms: 180000

  - id: verify-gate
    name: Fix Verification Gate
    type: condition
    dependsOn: [verify-fix]
    config:
      expression: "steps.verify-fix.output.all_passed == true"
      on_true: complete
      on_false: escalate

  - id: escalate
    name: Escalate to Human
    type: gate
    dependsOn: [verify-gate]
    when: "steps.verify-gate.result == 'false'"
    config:
      auto_approve: false
      notify: [owner]
      message: |
        Automated fix failed verification after
        \${{params.max_fix_attempts}} attempts.
        Manual intervention required.
      timeout_ms: 0

  - id: complete
    name: Mark Complete
    type: agent-task
    dependsOn: [verify-gate]
    when: "steps.verify-gate.result == 'true'"
    config:
      agent_source: claude-code
      prompt: |
        Fix verified. Create a summary:
        - Root cause
        - Fix applied
        - Tests added
        - Any follow-up recommendations

triggers:
  - trigger:
      type: event
      eventType: run.failed
      filters:
        source: claude-code
    enabled: true
    priority: 2
    rateLimit:
      debounceMs: 120000
      maxFires: 3
      windowMs: 3600000

  - trigger:
      type: conditional
      condition: "run.metrics.errorCount > 5 && run.status == 'live'"
      pollIntervalMs: 30000
      deduplicate: true
    enabled: true
    priority: 1

  - trigger:
      type: manual
      buttonLabel: Debug Issue
      requiredInputs: [issue_description]
      confirmMessage: "Start debug workflow for this issue?"
    enabled: true
    priority: 0

defaults:
  timeoutMs: 300000
  retryPolicy:
    maxAttempts: 2
    backoffMs: 5000
    backoffMultiplier: 2
  onFailure: continue
`;

// ─── Template Discovery & Instantiation ─────────────────────

/**
 * How agents discover and instantiate templates at runtime.
 * 
 * Discovery flow:
 * 1. Agent queries registry by tags/intent
 * 2. Registry returns matching templates with metadata
 * 3. Agent selects template, provides parameters
 * 4. Registry validates parameters, resolves composition
 * 5. Returns a WorkflowInstance ready to execute
 */
export interface TemplateInstantiationRequest {
  /** Template to instantiate */
  templateId: string;
  /** Specific version (latest if omitted) */
  version?: string;
  /** Parameter values */
  parameters: Record<string, unknown>;
  /** Slot fills (slot name → steps to inject) */
  slotFills?: Record<string, TemplateStepDefinition[]>;
  /** Trigger overrides (disable/enable specific triggers) */
  triggerOverrides?: Record<number, { enabled: boolean }>;
  /** Who/what is instantiating this */
  initiator: {
    type: 'agent' | 'user' | 'trigger' | 'workflow';
    id: string;
    runId?: string;
  };
}

export interface TemplateInstantiationResult {
  success: boolean;
  instance?: WorkflowInstance;
  errors?: Array<{
    field: string;
    message: string;
    code: 'missing_required' | 'validation_failed' | 'template_not_found' | 'version_mismatch';
  }>;
}

/**
 * Agent-facing template discovery interface.
 * Exposed via tool definitions so agents can search/instantiate workflows.
 */
export interface AgentTemplateAPI {
  /** Search templates by natural language query or tags */
  searchTemplates(query: string, options?: {
    tags?: string[];
    origin?: 'builtin' | 'user' | 'discovered';
    limit?: number;
  }): TemplateRegistryEntry[];

  /** Get full template definition */
  getTemplate(id: string, version?: string): WorkflowTemplate | null;

  /** Validate parameters without instantiating */
  validateParameters(
    templateId: string,
    parameters: Record<string, unknown>,
  ): { valid: boolean; errors: string[] };

  /** Instantiate template into a runnable workflow */
  instantiate(request: TemplateInstantiationRequest): TemplateInstantiationResult;

  /** List running instances of a template */
  listInstances(templateId: string, status?: string): WorkflowInstance[];
}
