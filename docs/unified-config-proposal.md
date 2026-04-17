# Unified Configuration: APL + Plugin Routing + Plugin Framework

**Status:** Proposal
**Builds on:** APL DSL Spec, Plugin Routing Proposal, Plugin Framework Spec v2

## Vision

A single configuration file defines plugin declarations, routing rules, policy, transforms, and orchestration, all expressed in APL syntax.

Plugins are first-class citizens in APL pipe chains and policy blocks, callable inline alongside built-in operations like `redact`, `mask`, and `deny`. Orchestration modes (sequential, parallel) are declared in the same document.

## What Unifies

| Before (scattered) | After (unified) |
|---|---|
| `plugins/config.yaml` for plugin declarations | `config.yaml`, one file |
| `apl/policy.yaml` for APL policy rules | Embedded in routes |
| Plugin conditions (tools, server_ids) | Route matching (name, tags, when) |
| Per-plugin execution modes | Inline orchestration in policy blocks |
| Separate policy engines (OPA, Cedar, NeMo) | `opa(...)`, `cedar:`, `nemo(...)` in policy blocks |
| Bespoke plugin invocations | `plugin(name)` in pipe chains and policy blocks |

## Configuration Structure

```yaml
# ══════════════════════════════════════════════════════════════
# 1. GLOBAL — applies to every request
# ══════════════════════════════════════════════════════════════

global:
  identity:
    provider: cedarling                    # or: jwt, spiffe, custom
    config:
      trusted_issuers:
        Corporate:
          openid_configuration_endpoint: "https://keycloak.corp.com/.well-known/openid-configuration"
          token_metadata:
            access_token:
              entity_type_name: "Jans::Access_token"
              principal_mapping: ["Jans::Workload"]
            id_token:
              entity_type_name: "Jans::Id_token"
              user_id: "sub"
              principal_mapping: ["Jans::User"]

  session:
    store: memory                          # memory | redis | cedarling_data_api
    ttl: 3600

  delegation:
    max_depth: 3
    permission_reduction: monotonic

  defaults:
    tool:
      policy:
        - require(perm.tool_execute)
    prompt:
      policy:
        - require(authenticated)
    resource:
      policy:
        - require(authenticated)

  policies:
    all:
      description: "Baseline rules applied to every request"
      policy:
        - require(authenticated)
    pii:
      description: "PII data access controls"
      metadata:
        owner: security-team
        severity: high
      policy:
        - require(perm.pii_access)
    sensitive:
      description: "Sensitive data with delegation restrictions"
      metadata:
        owner: compliance-team
      policy:
        - require(perm.sensitive_access)
        - delegation.depth > 2: deny


# ══════════════════════════════════════════════════════════════
# 2. PLUGINS — declare available plugins (templates)
# ══════════════════════════════════════════════════════════════

plugins:
  # --- Built-in (compiled into runtime) ---
  - name: apl
    kind: builtin
    description: "APL attribute-based policy engine"

  - name: cedarling
    kind: builtin
    description: "Cedarling identity + Cedar PDP"

  # --- Rust native plugins ---
  - name: rate_limiter
    kind: "native://plugins/rate_limiter.so"
    capabilities: ["read_subject"]
    config:
      max_requests: 100
      window_seconds: 60

  - name: audit_logger
    kind: "native://plugins/audit_logger.so"
    capabilities: ["read_subject", "read_labels"]
    config:
      destination: "lock_server"

  # --- WASM sandboxed plugins ---
  - name: pii_scanner
    kind: "wasm://plugins/pii_scanner.wasm"
    capabilities: ["read_subject", "append_labels"]

  - name: custom_validator
    kind: "wasm://plugins/validator.wasm"
    capabilities: []

  # --- Python plugins (backward compat) ---
  - name: legacy_filter
    kind: "python://plugins.legacy.filter.LegacyFilterPlugin"
    capabilities: ["read_subject"]
    config:
      filter_mode: strict

  # --- External PDP plugins ---
  - name: nemo_guardrails
    kind: nemo
    config:
      url: "http://nemo-guardrails:8000/v1/guardrail/checks"
      default_config_id: "prompt-injection"

  - name: company_opa
    kind: opa
    config:
      url: "http://opa:8181/v1/data"

  - name: corp_authzen
    kind: authzen
    config:
      url: "https://authz.corp.com/access/v1/evaluation"

  - name: cedar_pdp
    kind: cedar
    config:
      policy_store: "/etc/cedar/hr-policies.cjar"


# ══════════════════════════════════════════════════════════════
# 3. ROUTES — per-entity policy, transforms, and plugins
# ══════════════════════════════════════════════════════════════

routes:
  # ────────────────────────────────────────────────────────
  # HR Compensation — full policy with inline plugins
  # ────────────────────────────────────────────────────────
  - tool: get_compensation
    meta:
      tags: [pii, hr]
    taint:
      session: [PII, financial]
      message: [contains_compensation]

    # Route-level plugin config overrides (scoped to this route)
    plugins:
      rate_limiter:
        max_requests: 10            # stricter than global 100
        window_seconds: 30
      pii_scanner:
        sensitivity: high
        scan_fields: [ssn, salary, notes]

    args:
      employee_id: "str"
      include_ssn: "bool"

    policy:
      # Built-in APL rules (sub-ms, in-process)
      - args.include_ssn == true & !perm.view_ssn: deny
      - delegation.depth > 1: deny
      - session.labels contains "PII": deny

      # NeMo guardrails — prompt injection check with side effects
      - nemo(args.query):
          config_id: "prompt-injection"
          on_deny:
            - deny
            - taint(injection_attempt, [session, message])
            - plugin(security_alert)

      # Cedar RBAC policy via Cedarling with side effects
      - cedar:
          action: "Jans::Action::Read"
          resource_type: "Jans::CompensationRecord"
          on_deny:
            - deny
            - plugin(compliance_alert)
          on_allow:
            - taint(compensation_accessed, session)

      # OPA rule group — URL selects the rule set
      - opa("http://opa:8181/v1/data/hr/compensation/deny"):
          on_deny:
            ssn_blocked:
              - deny
              - taint(SSN_DENIED, session)
            _default:
              - deny
              - plugin(audit_logger)

      # Plugin orchestration — run these sequentially on pre-invoke
      - sequential:
          - plugin(rate_limiter)
          - plugin(audit_logger)

      # Unconditional taint — always track that this tool was accessed
      - taint(compensation_accessed, session)

    result:
      ssn: "str | redact(!perm.view_ssn) | taint(PII, [session, message])"
      salary: "int | redact(!role.hr)"
      internal_notes: "omit"
      employee_id: "str | mask(4)"
      # Plugin in pipe chain — PII scanner on free text fields
      notes: "str | plugin(pii_scanner) | nemo(pii-sanitize)"

    post_policy:
      # Unconditional — always audit after tool execution
      - plugin(audit_logger)
      - result.employee_id | mask(4)

      # Conditional taint based on result content
      - result.record_count > 100: taint(bulk_access, session)

  # ────────────────────────────────────────────────────────
  # Email — forwarding action with session taint check
  # ────────────────────────────────────────────────────────
  - tool: send_email
    auth_enforced_by: target

    policy:
      - require(perm.email_send)
      - session.labels contains "PII": deny
      - plugin(custom_validator)

  # ────────────────────────────────────────────────────────
  # Tag-based routing — all tools tagged 'sensitive'
  # ────────────────────────────────────────────────────────
  - tool: "*"
    meta:
      tags: [sensitive]

    policy:
      - require(authenticated)
      - sequential:
          - plugin(pii_scanner)
          - plugin(audit_logger)

    result:
      "*": "str | plugin(pii_scanner)"     # scan all string fields

  # ────────────────────────────────────────────────────────
  # Catch-all — default plugins for all tools
  # ────────────────────────────────────────────────────────
  - tool: "*"
    policy:
      - plugin(rate_limiter)
      - plugin(audit_logger)


```

## The `plugin()` Functor

`plugin(name)` is a first-class operation in APL, usable in three contexts that match the plugin kinds defined in the APL spec (Section 4.7): pipe chains (field plugins), policy blocks (decision plugins), and `on_deny` / `on_allow` blocks (reaction plugins).

### In Pipe Chains (field plugins)

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  body:  "str | plugin(pii_scanner) | nemo(pii-sanitize)"
```

The plugin receives the field value and returns an (optionally) modified value. It acts as a modifier, like `redact` or `mask`, but with plugin-defined logic.

The plugin's `execute()` method receives:
- `payload`: a `FieldPayload` with the field name, value, type, tool name, and phase
- `context`: plugin context with global state
- `extensions`: capability-filtered extensions

It returns a `PluginResult` that may carry a `modified_payload`, taint labels, or a deny violation.

### In Policy Blocks (decision plugins and orchestration)

```yaml
policy:
  # Single plugin as a decision point
  - plugin(rate_limiter)

  # Conditional plugin invocation
  - args.include_ssn == true: plugin(pii_scanner)

  # Sequential orchestration (unconditional)
  - sequential:
      - plugin(rate_limiter)
      - plugin(audit_logger)
      - plugin(custom_validator)

  # Parallel orchestration (unconditional)
  - parallel:
      - plugin(pii_scanner)
      - plugin(nemo_guardrails)

  # Conditional with multiple actions (list = sequential by default)
  - delegation.depth > 1:
      - plugin(audit_logger)
      - plugin(compliance_checker)
      - taint(delegated_access)

  # Conditional with parallel
  - args.include_ssn == true:
      parallel:
        - plugin(pii_scanner)
        - plugin(nemo_guardrails)

  # Conditional with mixed actions
  - !role.hr:
      - plugin(audit_logger)              # sequential: runs first
      - result.salary | redact            # then transform
      - taint(unauthorized_comp_access)   # then taint
```

**Execution semantics:**

| Form | Behavior |
|------|----------|
| `plugin(name)` | Run plugin; honor its decision (allow, deny, modify, taint) |
| `condition: plugin(name)` | Run plugin only when the condition holds |
| `condition:` + list | Conditional sequential: run list items in order when the condition holds |
| `condition:` + `parallel:` | Conditional parallel: run concurrently when the condition holds |
| `sequential: [...]` | Unconditional sequential: run in order, halt on first deny |
| `parallel: [...]` | Unconditional parallel: run concurrently; deny if any branch denies |

**Plugin decisions in policy:**

When `plugin(name)` appears in a policy block the plugin receives the full `MessagePayload` and acts as a decision point. Per APL Section 4.7, it may:
- allow (pipeline continues)
- deny with a violation (pipeline halts, same semantics as APL `deny`)
- modify the payload (same semantics as APL transforms)
- emit taint labels against the session, the message, or both

### Combining APL Rules with Plugins

The power is in mixing them:

```yaml
policy:
  # Fast APL check first (sub-ms)
  - !perm.view_ssn & args.include_ssn == true: deny

  # Then Cedar for complex RBAC (sub-ms, in-process)
  - cedar:
      action: "Jans::Action::Read"
      resource_type: "Jans::CompensationRecord"

  # Then NeMo for content safety (slower, HTTP)
  - nemo(args.query):
      config_id: "prompt-injection"
      on_deny:
        - deny

  # Then custom plugins for business logic
  - sequential:
      - plugin(rate_limiter)
      - plugin(compliance_checker)

  # Finally, conditional taint
  - args.include_ssn == true: taint(SSN_REQUESTED)
```

Lines execute top to bottom: fast in-process checks first, slower external calls later. Any deny halts the pipeline. This is the tiered evaluation model from the routing proposal, expressed inline in APL.

## Route-Level Plugin Config Overrides

Plugins are declared globally in the `plugins:` section with default configuration. A route overrides that configuration for its own scope:

```yaml
# Global declaration
plugins:
  - name: rate_limiter
    kind: "native://plugins/rate_limiter.so"
    config:
      max_requests: 100
      window_seconds: 60

routes:
  # This route overrides the rate limiter config
  - tool: get_compensation
    plugins:
      rate_limiter:
        max_requests: 10          # stricter for this sensitive tool
        window_seconds: 30
    policy:
      - plugin(rate_limiter)      # uses max_requests: 10

  # This route uses the global config
  - tool: get_directory
    policy:
      - plugin(rate_limiter)      # uses max_requests: 100
```

**Config layering (most specific wins):**

```
Global plugin declaration (plugins: section at root)
  └── Route-level plugin block (overrides for this route)
```

The route-level `plugins:` block is a map from plugin name to config overrides. Only the specified keys are overridden; everything else inherits from the global declaration. A route may also override capabilities if a plugin needs extra access for specific tools:

```yaml
routes:
  - tool: process_payment
    plugins:
      audit_logger:
        config:
          log_level: "detailed"
          include_args: true
        capabilities: ["read_subject", "read_labels", "read_headers"]  # extra cap for this route
```

## FieldPayload Hook

When `plugin(name)` appears in a pipe chain, the plugin receives a `FieldPayload`, a lightweight payload type for field-level operations:

```rust
struct FieldPayload {
    field_name: String,       // "ssn", "salary", "notes"
    field_value: Value,       // current value (after prior pipe steps)
    field_type: String,       // "str", "int", "bool"
    tool_name: String,        // tool this field belongs to
    phase: Phase,             // Args (pre-invoke) or Result (post-invoke)
}
```

The plugin processes the field and returns an (optionally modified) value, plus optional taint labels or a deny violation:

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  #              ^                     ^
  #              FieldPayload:         Standard APL transform
  #                field_name: "notes"
  #                field_value: "Performance review pending..."
  #                field_type: "str"
  #                phase: Result
  #
  #              Plugin result, any of:
  #                modified_payload: FieldPayload { field_value: "[PII detected] ..." }
  #                taint: { labels: ["PII"], scope: [session, message] }
  #                deny (validation failure)
```

The same `Plugin` trait backs both policy decisions (which receive `MessagePayload`) and field transforms (which receive `FieldPayload`). The hook type determines the payload type:

| Hook | Payload | Plugin receives |
|------|---------|----------------|
| `cmf.tool_pre_invoke` | `MessagePayload` | Full tool call with args |
| `cmf.tool_post_invoke` | `MessagePayload` | Full tool result |
| `field_transform` | `FieldPayload` | Single field value |

Plugins declare which hooks they support. A single plugin may support both message-level and field-level hooks.

## Route Matching

Routes match using the same model as the plugin routing proposal:

| Matcher | Speed | Example |
|---------|-------|---------|
| Exact name + scope | O(1) hash | `tool: get_compensation` with `meta.scope: hr-services` |
| Exact name | O(1) hash | `tool: get_compensation` |
| Name list | O(n) hash lookups | `tool: [create_user, update_user, delete_user]` |
| Glob pattern | O(n) glob match | `tool: "hr-server-*"` |
| Meta tag match | O(1) set intersection | `meta.tags contains 'pii'` |
| When expression | Evaluated at runtime | `when: "meta.scope == 'production'"` |
| Wildcard | Catch-all | `tool: "*"` |
| Defaults | Fallback per entity type | `defaults.tool` |

Routes are evaluated in order. More specific routes (exact name + scope) take precedence over less specific matches (meta tag), which take precedence over wildcards and defaults.

Tags declared on a route via `meta.tags` serve two purposes: (1) they are assigned to the entity's `MetaExtension` for policy condition evaluation; and (2) if a matching named policy group exists in `global.policies`, that policy is automatically inherited.

## Execution Model

### Plugin Kinds by Invocation Context

The plugin routing proposal defined modes on individual plugins. In the unified config the kind is determined by the invocation context, following APL Section 4.7:

| Invocation | Kind | Allowed effects |
|-----------|------|-----------------|
| `plugin(name)` in `policy:` or `post_policy:` | Decision plugin | allow, deny, modify, taint |
| `plugin(name)` in pipe chain (`args:` / `result:`) | Field plugin | modify, taint, deny, pass through |
| `plugin(name)` in `on_deny:` / `on_allow:` | Reaction plugin | taint, audit, alert (no deny override) |
| `sequential: [...]` | Ordered decision group | Runs branches in order; first deny halts; modifications chain |
| `parallel: [...]` | Concurrent decision group | Branches see the same input; taint unions; any deny denies; mutations are branch-local |

Kind is assigned by context, not by plugin declaration. The same plugin may act as a field plugin in one route and as a decision plugin in another, provided it implements the corresponding hooks.

### Ordering Guarantees

Within a policy block:
```yaml
policy:
  - rule_1          # runs first
  - rule_2          # runs second (if rule_1 didn't deny)
  - sequential:
      - plugin_a    # runs third
      - plugin_b    # runs fourth
  - rule_3          # runs fifth
```

Within a pipe chain:
```yaml
result:
  field: "str | plugin(a) | redact(!cond) | plugin(b)"
  #              runs 1st     runs 2nd        runs 3rd
```

## Migration from Existing Configs

### From Plugin Config YAML

```yaml
# Before: plugins/config.yaml
plugins:
  - name: "PiiFilter"
    kind: "plugins.pii.PiiFilterPlugin"
    hooks: ["tool_pre_invoke", "tool_post_invoke"]
    mode: "sequential"
    priority: 50
    conditions:
      - tools: ["get_compensation"]
    config:
      redaction_char: "*"

# After: unified config.yaml
plugins:
  - name: pii_filter
    kind: "python://plugins.pii.PiiFilterPlugin"
    capabilities: ["read_subject", "append_labels"]
    config:
      redaction_char: "*"

routes:
  - tool: get_compensation
    result:
      "*": "str | plugin(pii_filter)"
```

Conditions map to route matching. Hooks map to invocation context (policy vs pipe chain). Priority maps to list order.

### From APL Policy YAML

```yaml
# Before: apl/policy.yaml (standalone)
routes:
  - tool: get_compensation
    policy:
      - args.include_ssn == true & !perm.view_ssn: deny
    result:
      salary: "redact(!role.hr)"

# After: unified config.yaml (embedded in route)
routes:
  - tool: get_compensation
    policy:
      - args.include_ssn == true & !perm.view_ssn: deny
      - plugin(pii_scanner)            # NEW: plugin inline
    result:
      salary: "int | redact(!role.hr)"
      notes: "str | plugin(pii_scanner)"  # NEW: plugin in pipe chain
```

APL policy embeds unchanged. Plugins are added where needed, as field transforms in pipe chains or as decision points in policy blocks.

## Open Questions

1. **Parallel + modify**: APL Section 6.3 states that mutations from one branch are not visible to siblings and taint labels union monotonically. This leaves the merge order of concurrent mutations undefined. Proposal: restrict `parallel:` blocks to validation and taint; if any branch returns `modified_payload`, reject the policy at compile time.

2. **Wildcard field matching**: Is `"*": "str | plugin(pii_scanner)"` the right syntax for "scan all string fields", and how should non-string fields be skipped? Should there be typed wildcards (`str:*`, `int:*`) or a predicate matcher?

3. **Error handling per invocation**: If `plugin(rate_limiter)` crashes, should the pipeline fail closed or fail open? Should the policy be configurable per-plugin globally and overridable per-route, and what is the default?

4. **External PDP declarations vs inline URLs**: The `plugins:` section declares `company_opa` with `config.url`, but route policy uses `opa("http://…")` with an inline URL. Should `opa(company_opa)` resolve the declared plugin's URL, should the inline form remain for ad-hoc calls, or should both be supported with a precedence rule?

5. **FieldPayload batch mode**: Should there be a `FieldsPayload` for plugins that need to see all fields at once (e.g., cross-field validation), or is per-field invocation sufficient?

6. **Route-level capability override security**: Should a route be allowed to grant capabilities beyond what the global declaration specifies, or only narrow them? Granting extra capabilities locally makes per-route behavior harder to audit; narrowing them is safe by construction.

7. **Config override depth**: Route-level plugin config is a shallow merge. Should deep merge be supported for nested config, so a route can override `config.rules[0].threshold` without replacing the entire `rules` list?
