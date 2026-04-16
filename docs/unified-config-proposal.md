# Unified Configuration: APL + Plugin Routing + Plugin Framework

**Status:** Proposal
**Builds on:** APL DSL Spec, Plugin Routing Proposal, Plugin Framework Spec v2

## Vision

One configuration file that defines everything: plugin declarations, routing rules, policy, transforms, and plugin orchestration — all using APL's syntax as the unifying language.

Plugins become first-class citizens in APL's pipe chains and policy blocks, callable inline alongside built-in operations like `redact`, `mask`, and `deny`. The execution model (sequential, parallel) is expressed declaratively in the same YAML.

## What Unifies

| Before (scattered) | After (unified) |
|---|---|
| `plugins/config.yaml` — plugin declarations | `config.yaml` — one file |
| `apl/policy.yaml` — APL policy rules | Embedded in routes |
| Plugin conditions (tools, server_ids) | Route matching (name, tags, when) |
| Separate execution modes per plugin | Inline orchestration in policy blocks |
| Separate policy engines (OPA, Cedar, NeMo) | `opa:`, `cedar:`, `nemo:` in policy blocks |
| Separate plugin invocations | `plugin(name)` in pipe chains and policy blocks |

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

`plugin(name)` is a first-class operation in APL, usable in two contexts:

### In Pipe Chains (transforms)

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  body:  "str | plugin(pii_scanner) | nemo(pii-sanitize)"
```

The plugin receives the field value, processes it (scan, transform, validate), and returns the modified value. It's a **modifier** — same as `redact` or `mask`, but the logic is in the plugin.

**Execution:** The plugin's `execute()` method receives:
- `payload`: A `FieldPayload` with the field name, value, and type
- `context`: Plugin context with global state
- `extensions`: Capability-filtered extensions

**Return:** `PluginResult` with optional `modified_payload` containing the transformed value.

### In Policy Blocks (decisions + orchestration)

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
| `plugin(name)` | Run plugin, use its decision (allow/deny/modify) |
| `condition: plugin(name)` | Run plugin only if condition is true |
| `condition:` + list | Conditional sequential — run list items in order if condition met |
| `condition:` + `parallel:` | Conditional parallel — run concurrently if condition met |
| `sequential: [...]` | Unconditional sequential — run in order, stop on first deny |
| `parallel: [...]` | Unconditional parallel — run concurrently, all must allow |

**Plugin decisions in policy:**

When `plugin(name)` appears in a policy block, the plugin acts as a **decision point**:
- Returns `allow` → pipeline continues
- Returns `deny` (with violation) → pipeline halts (same as APL `deny`)
- Returns `modify` → payload updated (same as APL transforms)
- Returns `taint` → session labels updated

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
      on_match: deny

  # Then custom plugins for business logic
  - sequential:
      - plugin(rate_limiter)
      - plugin(compliance_checker)

  # Finally, conditional taint
  - args.include_ssn == true: taint(SSN_REQUESTED)
```

Each line executes in order. Fast checks first, slower external calls later. If any step denies, the pipeline halts. This is the tiered evaluation model from the routing proposal, but expressed inline in APL.

## Route-Level Plugin Config Overrides

Plugins are declared globally in the `plugins:` section with default configuration. Routes can override that configuration for their scope:

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

The route-level `plugins:` block is a map of plugin name → config overrides. Only the keys you specify are overridden; everything else inherits from the global declaration. You can also override capabilities per-route if a plugin needs extra access for specific tools:

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

When `plugin(name)` appears in a pipe chain, the plugin receives a `FieldPayload` — a lightweight payload type for field-level operations:

```rust
struct FieldPayload {
    field_name: String,       // "ssn", "salary", "notes"
    field_value: Value,       // current value (after prior pipe steps)
    field_type: String,       // "str", "int", "bool"
    tool_name: String,        // which tool this field belongs to
    phase: Phase,             // Args (pre-invoke) or Result (post-invoke)
}
```

The plugin processes the field and returns the (optionally modified) value:

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  #              ↑                     ↑
  #              FieldPayload:         Standard APL transform
  #                field_name: "notes"
  #                field_value: "Performance review pending..."
  #                field_type: "str"
  #                phase: Result
  #
  #              Plugin returns:
  #                modified_payload: FieldPayload { field_value: "[PII detected] ..." }
  #                or taint: { labels: ["PII"] }
  #                or deny (validation failure)
```

This means the same `Plugin` trait works for both policy decisions (receives `MessagePayload`) and field transforms (receives `FieldPayload`). The hook type determines the payload type:

| Hook | Payload | Plugin receives |
|------|---------|----------------|
| `cmf.tool_pre_invoke` | `MessagePayload` | Full tool call with args |
| `cmf.tool_post_invoke` | `MessagePayload` | Full tool result |
| `field_transform` | `FieldPayload` | Single field value |

Plugins declare which hooks they support. A plugin can support both message-level and field-level hooks.

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

Routes are evaluated in order. More specific routes (exact name + scope) take precedence over less specific (meta tag match) which take precedence over wildcards and defaults.

Tags declared on a route via `meta.tags` serve two purposes: (1) they are assigned to the entity's `MetaExtension` for policy condition evaluation, and (2) if a matching named policy group exists in `global.policies`, that policy is automatically inherited.

## Execution Model

### Plugin Modes in Unified Config

The plugin routing proposal defined modes on individual plugins. In the unified config, modes are defined by **how** the plugin is invoked:

| Invocation | Mode | Can deny? | Can modify? |
|-----------|------|-----------|-------------|
| `plugin(name)` in policy | validate | Yes | No |
| `plugin(name)` in pipe chain | modify | No | Yes |
| `plugin(name)` in post_policy | observe | No | No (but can taint) |
| `sequential: [...]` | validate (ordered) | Yes (stops on deny) | Yes (chained) |
| `parallel: [...]` | validate (concurrent) | Yes (all must allow) | No (no chaining) |

The mode is determined by context, not by plugin declaration. The same plugin can validate in one route and modify in another.

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

Conditions become route matching. Hooks become invocation context (policy vs pipe chain). Priority becomes list order.

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

APL policy embeds unchanged. Plugins are added where needed.

## Open Questions

1. **Parallel + modify**: Can parallel plugins modify the payload? If two plugins modify the same field concurrently, who wins? Proposal: parallel plugins can only validate (allow/deny), not modify.

2. **Wildcard field matching**: `"*": "str | plugin(pii_scanner)"` scans all string fields. Is this the right syntax? What about non-string fields?

3. **Error handling per invocation**: If `plugin(rate_limiter)` fails (plugin crashes), should the pipeline deny (fail-closed) or continue (fail-open)? Configurable per-plugin globally, or overridable per-route in the plugin block?

4. **Plugin as taint source**: Can `plugin(name)` in a policy block return taint labels? e.g., a PII scanner plugin detects PII and returns `taint(PII)` instead of deny.

5. **FieldPayload batch mode**: Should there be a `FieldsPayload` for plugins that want to see all fields at once (e.g., cross-field validation)? Or is per-field sufficient?

6. **Route-level capability override security**: Should routes be allowed to grant capabilities beyond what the global declaration specifies? Or only narrow (remove capabilities)?

7. **Config override depth**: Route-level plugin config is a shallow merge. Should deep merge be supported for nested config? e.g., override `config.rules[0].threshold` without replacing the entire `rules` list.
