# Unified Configuration: APL + Plugin Routing + Plugin Framework

**Status:** Proposal
**Dependencies:** [APL Specification](../docs/specs/apl-dsl-spec.md)

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

A unified config has four top-level regions: `global`, `plugin_dirs`, `plugins`, and `routes`. The three examples below introduce them in layers. Appendix B combines them into a single end-to-end config.

### Example 1: Baseline policy

Identity, session, defaults, and one tool route. Pure APL, no plugins.

```yaml
global:
  identity:
    provider: cedarling
    config:
      trusted_issuers:
        Corporate:
          openid_configuration_endpoint: "https://keycloak.corp.com/.well-known/openid-configuration"

  session:
    store: memory
    ttl: 3600

  defaults:
    tool:
      policy:
        - require(perm.tool_execute)

routes:
  - tool: get_employee
    args:
      employee_id: "str"
    policy:
      - require(authenticated)
      - delegation.depth > 2: deny
    result:
      ssn: "str | redact(!perm.view_ssn)"
      salary: "int | redact(!role.hr)"
      employee_id: "str | mask(4)"
```

### Example 2: External PDP delegation

Adds OPA, Cedar, and NeMo declarations. The route invokes each PDP via its declared name: the plugin section carries the URL and timeout, the policy block carries the call.

```yaml
plugins:
  - name: company_opa
    kind: opa
    config:
      url: "http://opa:8181/v1/data"
      timeout_ms: 500

  - name: cedar_pdp
    kind: cedar
    config:
      policy_store: "/etc/cedar/hr-policies.cjar"

  - name: nemo_guardrails
    kind: nemo
    config:
      url: "http://nemo:8000/v1/guardrail/checks"
      default_config_id: "prompt-injection"
      on_error: deny

routes:
  - tool: get_compensation
    policy:
      - !perm.view_ssn & args.include_ssn == true: deny

      - nemo(args.query):
          on_deny:
            - deny

      - cedar:
          action: "Jans::Action::Read"
          resource_type: "Jans::CompensationRecord"

      - opa(company_opa, "hr/compensation/deny"):
          on_deny:
            - deny
```

### Example 3: Plugin orchestration

Adds native, WASM, and Python plugins. The route uses `sequential:`, a pipe-chain field plugin, an `on_deny:` reaction, and a route-level config override.

```yaml
plugin_dirs:
  - ./plugins

plugins:
  - name: rate_limiter
    kind: native
    source: "plugins/rate_limiter.so"
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject"]
    on_error: fail
    config:
      max_requests: 100
      window_seconds: 60

  - name: audit_logger
    kind: native
    source: "plugins/audit_logger.so"
    hooks: [tool_pre_invoke, tool_post_invoke]
    capabilities: ["read_subject", "read_labels"]
    on_error: ignore

  - name: pii_scanner
    kind: wasm
    source: "plugins/pii_scanner.wasm"
    hooks: [tool_pre_invoke, field_transform]
    capabilities: ["read_subject", "append_labels"]

  - name: security_alert
    kind: plugins.alerting.SecurityAlertPlugin
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject", "read_labels"]

routes:
  - tool: get_compensation
    meta:
      tags: [pii, hr]
    taint:
      session: [PII, financial]

    plugins:
      rate_limiter:
        config:
          max_requests: 10            # stricter than the global 100
          window_seconds: 30

    policy:
      - sequential:
          - plugin(rate_limiter)
          - plugin(audit_logger)

      - opa(company_opa, "hr/compensation/deny"):
          on_deny:
            - deny
            - plugin(security_alert)

    result:
      ssn: "str | redact(!perm.view_ssn) | taint(PII, [session, message])"
      notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"

    post_policy:
      - plugin(audit_logger)
      - exists(result.ssn) & !perm.view_ssn: deny
```

A complete end-to-end config combining these three layers is in [Appendix B](#appendix-b-complete-example).

## Plugin Declaration

Each entry under `plugins:` follows `cpex.framework.models.PluginConfig`. The unified config keeps the CPEX shape and adds a handful of new fields for APL integration. The table below is the canonical field reference; fields marked *new* are introduced by this proposal.

| Field | Type | Source | Description |
|---|---|---|---|
| `name` | string | CPEX | Unique plugin name, referenced from routes by `plugin(name)` and by declared-name PDP calls. |
| `kind` | string (tagged) | CPEX + new | See the *Kind grammar* table below. |
| `source` | string | *new* | Path to a binary artifact. Required for `kind: native` and `kind: wasm`; unused otherwise. |
| `description` | string | CPEX | Human-readable summary. |
| `version` | string | CPEX | Semver string. |
| `hooks` | list[string] | CPEX | CPEX hook names the plugin implements. Omit for `builtin` and PDP kinds. |
| `capabilities` | list[string] | *new* | Attribute-extension capabilities (`read_subject`, `read_labels`, `append_labels`, `read_headers`). Enforced by the runtime when materializing `extensions` for the plugin. |
| `on_error` | `fail \| ignore \| disable` | CPEX | Error-handling policy. Defaults to `fail`. Overridable per route. |
| `priority` | int | CPEX (deprecated here) | Kept for backward compatibility with CPEX configs loaded as-is. The unified config uses list order inside each policy block for ordering; set `priority` only when loading a legacy CPEX config. |
| `mode` | `PluginMode` | CPEX (see below) | See *Execution modes* below for how CPEX modes map to invocation contexts. |
| `conditions` | list[`PluginCondition`] | CPEX (subsumed) | Not used in the unified config; conditions are expressed through route matching (`tool:`, `meta.tags`, `when:`). A loader that ingests legacy CPEX configs preserves them. |
| `applied_to` | `AppliedTo` | CPEX (subsumed) | Not used in the unified config; field targeting is expressed through route `args:` / `result:` pipe chains. |
| `config` | map | CPEX | Opaque per-plugin configuration. Fully overridable per route. |
| `mcp` / `grpc` / `unix_socket` | transport blocks | CPEX | Required for `kind: external`. Unchanged. |

### Kind grammar

| `kind` | Artifact | Required extra fields | Notes |
|---|---|---|---|
| `builtin` | Compiled into the runtime | none | Used for engines shipped with the runtime (APL, Cedarling). |
| `native` | Rust dynamic library | `source:` (path) | Loaded via `dlopen`. |
| `wasm` | WASM module | `source:` (path) | Executed in a WASM sandbox. |
| *any FQN* (e.g. `plugins.pii.PiiFilter`) | Python class | none | Existing CPEX pattern. Loader resolves the class by import. |
| `external` | Out-of-process plugin | `mcp` / `grpc` / `unix_socket` block | Existing CPEX pattern. |
| `isolated_venv` | Python plugin in its own venv | `config.class_name`, `config.requirements_file`, `config.script_path` | Existing CPEX pattern. |
| `opa`, `cedar`, `authzen`, `nemo` | External PDP | `config.url` (or `config.policy_store` for Cedar) | Invoked from policy blocks as `opa(name, path)`, `cedar:`, `authzen(name)`, `nemo(name, field)`. |

### Execution modes

CPEX defines five execution modes on `PluginConfig.mode`. In the unified config the mode is inferred from the invocation context rather than set on the plugin, but the underlying CPEX scheduler still runs the plugin in one of those modes:

| Invocation context | CPEX `PluginMode` | Rationale |
|---|---|---|
| `plugin(name)` in `policy:` or as a `sequential:` branch | `sequential` | Serial, chained, can block and modify. |
| `plugin(name)` in pipe chain (`args:` / `result:`) | `transform` | Serial, chained, modifies payload. A field plugin can still return a deny violation (APL §4.7); CPEX suppresses blocking for `transform`, so the unified runtime reinstates the block when the plugin targets the `field_transform` hook. |
| `plugin(name)` in `post_policy:` | `audit` | Observational. Modifications are discarded; taint and violations are recorded. |
| `plugin(name)` in `parallel:` | `concurrent` | Parallel, fail-fast; modifications discarded (see Open Question 1). |
| `plugin(name)` in `on_deny:` / `on_allow:` | `audit` or `fire_and_forget` | Reaction plugins do not override the decision; audit (sync) and fire-and-forget (background) are both legal. The route picks the one it needs with an explicit `mode:` override inside the reaction block if needed. |

A plugin declaration that also sets `mode:` explicitly (for example, to force `fire_and_forget` on a telemetry plugin) is honored; the invocation context provides the default.

### Hook dispatch

When `plugin(name)` appears in a route, the loader resolves it to a specific CPEX hook:

| Invocation site | Hook dispatched | Payload |
|---|---|---|
| `policy:` on a `tool:` route | `tool_pre_invoke` | `ToolPreInvokePayload` |
| `post_policy:` on a `tool:` route | `tool_post_invoke` | `ToolPostInvokePayload` |
| Pipe chain (`args:` or `result:`) on any route | `field_transform` | `FieldPayload` |
| `policy:` on a `resource:` route | `resource_pre_fetch` | `ResourcePreFetchPayload` |
| `post_policy:` on a `resource:` route | `resource_post_fetch` | `ResourcePostFetchPayload` |
| `policy:` on a `prompt:` route | `prompt_pre_fetch` | `PromptPreFetchPayload` |
| `post_policy:` on a `prompt:` route | `prompt_post_fetch` | `PromptPostFetchPayload` |

If a plugin referenced by a route does not list the dispatched hook in its `hooks:` field, the configuration is rejected at load time.

### Preserved from CPEX

The unified config is additive. CPEX runtime behavior carried over unchanged:

- `PluginConfig` object shape: same fields, same types, same validation.
- `PluginMode` values and scheduler semantics (`sequential`, `transform`, `audit`, `concurrent`, `fire_and_forget`, `disabled`).
- `OnError` semantics (`fail`, `ignore`, `disable`).
- `plugin_dirs` discovery and plugin loading order.
- `plugin_settings` keys: `parallel_execution_within_band`, `plugin_timeout`, `fail_on_plugin_error`, `enable_plugin_api`, `plugin_health_check_interval`.
- Hook dispatch payloads (`ToolPreInvokePayload`, `PromptPreFetchPayload`, and siblings).
- Transport blocks for out-of-process plugins (`mcp`, `grpc`, `unix_socket`).

What changed: a routing overlay (`routes:`) and three new attributes on the plugin declaration (`kind` additions, `source:`, `capabilities:`). Legacy CPEX configs load unchanged and run through the same scheduler.

## The `plugin()` Functor

`plugin(name)` is a first-class operation in APL, usable in three contexts that match the plugin kinds defined in the APL spec (Section 4.7): pipe chains (field plugins), policy blocks (decision plugins), and `on_deny` / `on_allow` blocks (reaction plugins).

### In Pipe Chains (field plugins)

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  body:  "str | plugin(pii_scanner) | plugin(custom_validator)"
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
  # Fast APL check first (sub-ms, in-process)
  - !perm.view_ssn & args.include_ssn == true: deny

  # Then Cedar for complex RBAC (sub-ms, in-process)
  - cedar:
      action: "Jans::Action::Read"
      resource_type: "Jans::CompensationRecord"

  # Then NeMo for content safety (HTTP call)
  - nemo(args.query):
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
    kind: native
    source: "plugins/rate_limiter.so"
    hooks: [tool_pre_invoke]
    config:
      max_requests: 100
      window_seconds: 60

routes:
  # This route overrides the rate limiter config
  - tool: get_compensation
    plugins:
      rate_limiter:
        config:
          max_requests: 10        # stricter for this sensitive tool
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

The route-level `plugins:` block is a map from plugin name to an override object. The override object always uses the same keys as a plugin declaration (`config:`, `capabilities:`, `on_error:`); bare key-value pairs are not merged into `config` implicitly. Only the keys present in the override replace the inherited values; everything else is inherited from the global declaration:

```yaml
routes:
  - tool: process_payment
    plugins:
      audit_logger:
        config:
          log_level: "detailed"
          include_args: true
        capabilities: ["read_subject", "read_labels", "read_headers"]
        on_error: fail            # stricter than the global 'ignore'
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

| Hook | Payload type | Plugin receives |
|------|---------|----------------|
| `tool_pre_invoke` | `ToolPreInvokePayload` (CPEX built-in) | Full tool call with args |
| `tool_post_invoke` | `ToolPostInvokePayload` (CPEX built-in) | Full tool result |
| `field_transform` | `FieldPayload` (new; registered by the runtime) | Single field value |

Plugins declare which hooks they support via the `hooks:` list in the plugin declaration, as in CPEX today. A single plugin may support both message-level and field-level hooks by listing them all.

## Route Matching

### Canonical field order

A route is a map with a fixed set of keys:

| Key | Role |
|---|---|
| `tool:` / `prompt:` / `resource:` / `agent:` | Entity selector. Exactly one is required. |
| `meta:` | Static metadata: `tags`, `scope`, and any user-defined keys used by `when:` expressions. |
| `when:` | Runtime predicate. Evaluated after the entity selector matches. |
| `taint:` | Session / message labels to attach on every invocation of this route. |
| `plugins:` | Route-scoped config overrides for declared plugins. |
| `args:` | Per-argument type and pipe chain. |
| `policy:` | Pre-invocation rules and decision plugins. |
| `result:` | Per-field type and pipe chain applied to the tool's response. |
| `post_policy:` | Post-invocation rules, audits, and conditional taints. |

Keys outside this set are rejected at load time. The order above is the recommended authoring order. The runtime is key-order-insensitive.

### Matchers

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

### Precedence tiebreaker

When more than one route matches a request, the runtime picks exactly one by working through the following rules in order:

1. **Specificity class.** Exact name + scope > exact name > name list > glob pattern > meta tag > `when:`-only > wildcard > defaults.
2. **Predicate count.** Within the same class, a route with more `when:` conjuncts beats one with fewer. A route with no `when:` loses to one with any `when:` that also matches.
3. **File order.** Within the same class and predicate count, the first route in file order wins. Later routes are skipped silently.

Example: both routes below can match a call to `get_compensation` in production; route A wins on rule 2.

```yaml
routes:
  # A — wins: same class (exact name), one extra when: conjunct
  - tool: get_compensation
    when: "meta.scope == 'production'"
    policy:
      - require(perm.prod_comp_read)

  # B — loses under rule 2; would have won under rule 3 alone
  - tool: get_compensation
    policy:
      - require(perm.comp_read)
```

### Matcher examples

```yaml
routes:
  # Name list: one route for several tools that share a policy
  - tool: [create_user, update_user, delete_user]
    meta: { tags: [admin] }
    policy:
      - require(perm.user_admin)

  # Glob: every tool whose name starts with 'hr-'
  - tool: "hr-*"
    meta: { tags: [hr] }
    policy:
      - require(perm.hr_read)

  # when: runtime predicate against meta / subject / session attributes
  - tool: "*"
    when: "meta.scope == 'production' & subject.type == 'agent'"
    policy:
      - session.cost > 1.0: deny
```

Tags declared on a route via `meta.tags` serve two purposes: (1) they are assigned to the entity's `MetaExtension` for policy condition evaluation; and (2) if a matching named policy group exists in `global.policies`, that policy is automatically inherited.

### Load-time validation

The config is rejected at load time on any of the following:

- A route references a plugin name that is not declared in the `plugins:` section.
- A route invokes `plugin(name)` on a hook (inferred from the invocation site) that the plugin does not list in its `hooks:` field.
- A route uses a key not in the canonical set.
- A plugin declaration of `kind: native` or `kind: wasm` is missing `source:`.
- A plugin declaration of `kind: external` is missing all transport blocks (`mcp`, `grpc`, `unix_socket`).

A plugin declared with a hook that no route ever references is loaded but dormant: a warning is emitted, not an error.

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
# Before: plugins/config.yaml (CPEX today)
plugins:
  - name: PiiFilter
    kind: plugins.pii.PiiFilterPlugin
    hooks: [tool_pre_invoke, tool_post_invoke]
    mode: sequential
    priority: 50
    conditions:
      - tools: [get_compensation]
    config:
      redaction_char: "*"

# After: unified config.yaml
plugins:
  - name: pii_filter
    kind: plugins.pii.PiiFilterPlugin              # unchanged: CPEX FQN
    hooks: [tool_pre_invoke, field_transform]      # add field_transform for pipe-chain use
    capabilities: ["read_subject", "append_labels"]
    config:
      redaction_char: "*"

routes:
  - tool: get_compensation
    result:
      "*": "str | plugin(pii_filter)"
```

The `kind` is preserved: a CPEX Python plugin is still identified by its fully-qualified class name. `conditions` is replaced by route matching (`tool: get_compensation`), `mode: sequential` is replaced by the invocation context, and `priority: 50` is replaced by list order inside the route's policy block.

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

4. **Inline-URL escape hatch for external PDPs**: The canonical form uses the declared plugin name: `opa(company_opa, "hr/compensation/deny")`. Should an ad-hoc inline form (`opa("http://…")` with a full URL bypassing any declaration) also be supported for quick experiments, or should every PDP call require a named declaration for auditability?

5. **FieldPayload batch mode**: Should there be a `FieldsPayload` for plugins that need to see all fields at once (e.g., cross-field validation), or is per-field invocation sufficient?

6. **Route-level capability override security**: Should a route be allowed to grant capabilities beyond what the global declaration specifies, or only narrow them? Granting extra capabilities locally makes per-route behavior harder to audit; narrowing them is safe by construction.

7. **Config override depth**: Route-level plugin config is a shallow merge. Should deep merge be supported for nested config, so a route can override `config.rules[0].threshold` without replacing the entire `rules` list?

## Appendix A: Schema sketch

The authoritative schema lives at `schemas/unified-config.json`. The sketch below covers the top-level shape and the plugin-declaration object. Types follow JSON Schema draft 2020-12.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://contextforge.io/schemas/unified-config.json",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "version": { "type": "string" },
    "global": { "$ref": "#/$defs/Global" },
    "plugin_dirs": {
      "type": "array",
      "items": { "type": "string" }
    },
    "plugin_settings": { "$ref": "#/$defs/PluginSettings" },
    "plugins": {
      "type": "array",
      "items": { "$ref": "#/$defs/PluginDeclaration" }
    },
    "routes": {
      "type": "array",
      "items": { "$ref": "#/$defs/Route" }
    }
  },
  "$defs": {
    "PluginDeclaration": {
      "type": "object",
      "required": ["name", "kind"],
      "additionalProperties": false,
      "properties": {
        "name":        { "type": "string" },
        "kind":        { "type": "string" },
        "source":      { "type": "string" },
        "description": { "type": "string" },
        "version":     { "type": "string" },
        "hooks": {
          "type": "array",
          "items": {
            "enum": [
              "tool_pre_invoke", "tool_post_invoke",
              "prompt_pre_fetch", "prompt_post_fetch",
              "resource_pre_fetch", "resource_post_fetch",
              "agent_pre_invoke", "agent_post_invoke",
              "field_transform"
            ]
          }
        },
        "capabilities": {
          "type": "array",
          "items": {
            "enum": ["read_subject", "read_labels", "append_labels", "read_headers"]
          }
        },
        "on_error":   { "enum": ["fail", "ignore", "disable"] },
        "mode":       { "enum": ["sequential", "transform", "audit", "concurrent", "fire_and_forget", "disabled"] },
        "priority":   { "type": "integer" },
        "conditions": { "type": "array" },
        "applied_to": { "type": "object" },
        "config":     { "type": "object" },
        "mcp":         { "type": "object" },
        "grpc":        { "type": "object" },
        "unix_socket": { "type": "object" }
      },
      "allOf": [
        { "if": { "properties": { "kind": { "enum": ["native", "wasm"] } } },
          "then": { "required": ["source"] } },
        { "if": { "properties": { "kind": { "const": "external" } } },
          "then": { "anyOf": [
            { "required": ["mcp"] },
            { "required": ["grpc"] },
            { "required": ["unix_socket"] }
          ] } }
      ]
    },
    "Route": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "tool":        { "oneOf": [{ "type": "string" }, { "type": "array", "items": { "type": "string" } }] },
        "prompt":      { "oneOf": [{ "type": "string" }, { "type": "array", "items": { "type": "string" } }] },
        "resource":    { "oneOf": [{ "type": "string" }, { "type": "array", "items": { "type": "string" } }] },
        "agent":       { "oneOf": [{ "type": "string" }, { "type": "array", "items": { "type": "string" } }] },
        "meta":        { "type": "object" },
        "when":        { "type": "string" },
        "taint":       { "type": "object" },
        "plugins":     { "type": "object" },
        "args":        { "type": "object" },
        "policy":      { "type": "array" },
        "result":      { "type": "object" },
        "post_policy": { "type": "array" }
      },
      "oneOf": [
        { "required": ["tool"] },
        { "required": ["prompt"] },
        { "required": ["resource"] },
        { "required": ["agent"] }
      ]
    },
    "Global":         { "type": "object" },
    "PluginSettings": { "type": "object" }
  }
}
```

The schema intentionally leaves `Global`, `PluginSettings`, and the per-plugin `config:` blob open; those are validated against plugin-specific schemas after the top-level validation passes.

## Appendix B: Complete example

The three-layer examples in the body isolate concerns. Below is the same HR-compensation scenario as a single config, kept as a reference for reviewers who want to see every section interacting.

```yaml
# ══════════════════════════════════════════════════════════════
# 1. GLOBAL — applies to every request
# ══════════════════════════════════════════════════════════════

global:
  identity:
    provider: cedarling
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
    store: memory
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
# 2. PLUGIN DIRECTORIES
# ══════════════════════════════════════════════════════════════

plugin_dirs:
  - ./plugins


# ══════════════════════════════════════════════════════════════
# 3. PLUGINS
# ══════════════════════════════════════════════════════════════

plugins:
  # --- Built-in ---
  - name: apl
    kind: builtin
    description: "APL attribute-based policy engine"

  - name: cedarling
    kind: builtin
    description: "Cedarling identity + Cedar PDP"

  # --- Rust native ---
  - name: rate_limiter
    kind: native
    source: "plugins/rate_limiter.so"
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject"]
    on_error: fail
    config:
      max_requests: 100
      window_seconds: 60

  - name: audit_logger
    kind: native
    source: "plugins/audit_logger.so"
    hooks: [tool_pre_invoke, tool_post_invoke]
    capabilities: ["read_subject", "read_labels"]
    on_error: ignore
    config:
      destination: "lock_server"

  # --- WASM ---
  - name: pii_scanner
    kind: wasm
    source: "plugins/pii_scanner.wasm"
    hooks: [tool_pre_invoke, tool_post_invoke, field_transform]
    capabilities: ["read_subject", "append_labels"]

  - name: custom_validator
    kind: wasm
    source: "plugins/validator.wasm"
    hooks: [tool_pre_invoke]
    capabilities: []

  # --- Python (CPEX FQN) ---
  - name: legacy_filter
    kind: plugins.legacy.filter.LegacyFilterPlugin
    hooks: [tool_pre_invoke, tool_post_invoke]
    capabilities: ["read_subject"]
    config:
      filter_mode: strict

  - name: security_alert
    kind: plugins.alerting.SecurityAlertPlugin
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject", "read_labels"]

  - name: compliance_alert
    kind: plugins.alerting.ComplianceAlertPlugin
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject", "read_labels"]

  - name: compliance_checker
    kind: plugins.compliance.ComplianceCheckerPlugin
    hooks: [tool_pre_invoke]
    capabilities: ["read_subject"]

  # --- External PDPs ---
  - name: nemo_guardrails
    kind: nemo
    config:
      url: "http://nemo-guardrails:8000/v1/guardrail/checks"
      default_config_id: "prompt-injection"
      timeout_ms: 500
      on_error: deny

  - name: company_opa
    kind: opa
    config:
      url: "http://opa:8181/v1/data"
      timeout_ms: 500

  - name: corp_authzen
    kind: authzen
    config:
      url: "https://authz.corp.com/access/v1/evaluation"
      timeout_ms: 500

  - name: cedar_pdp
    kind: cedar
    config:
      policy_store: "/etc/cedar/hr-policies.cjar"


# ══════════════════════════════════════════════════════════════
# 4. ROUTES
# ══════════════════════════════════════════════════════════════

routes:
  # HR Compensation — full policy with inline plugins
  - tool: get_compensation
    meta:
      tags: [pii, hr]
    taint:
      session: [PII, financial]
      message: [contains_compensation]

    plugins:
      rate_limiter:
        config:
          max_requests: 10
          window_seconds: 30
      pii_scanner:
        config:
          sensitivity: high
          scan_fields: [ssn, salary, notes]

    args:
      employee_id: "str"
      include_ssn: "bool"

    policy:
      - args.include_ssn == true & !perm.view_ssn: deny
      - delegation.depth > 1: deny
      - session.labels contains "PII": deny

      - nemo(args.query):
          on_deny:
            - deny
            - taint(injection_attempt, [session, message])
            - plugin(security_alert)

      - cedar:
          action: "Jans::Action::Read"
          resource_type: "Jans::CompensationRecord"
          on_deny:
            - deny
            - plugin(compliance_alert)
          on_allow:
            - taint(compensation_accessed, session)

      - opa(company_opa, "hr/compensation/deny"):
          on_deny:
            ssn_blocked:
              - deny
              - taint(SSN_DENIED, session)
            _default:
              - deny
              - plugin(audit_logger)

      - sequential:
          - plugin(rate_limiter)
          - plugin(audit_logger)

      - taint(compensation_accessed, session)

    result:
      ssn: "str | redact(!perm.view_ssn) | taint(PII, [session, message])"
      salary: "int | redact(!role.hr)"
      internal_notes: "omit"
      employee_id: "str | mask(4)"
      notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"

    post_policy:
      - plugin(audit_logger)
      - result.record_count > 100: taint(bulk_access, session)
      - exists(result.ssn) & !perm.view_ssn: deny

  # Email — forwarding action with session taint check
  - tool: send_email
    policy:
      - require(perm.email_send)
      - session.labels contains "PII": deny
      - plugin(custom_validator)

  # Tag-based routing — all tools tagged 'sensitive'
  - tool: "*"
    meta:
      tags: [sensitive]
    policy:
      - require(authenticated)
      - sequential:
          - plugin(pii_scanner)
          - plugin(audit_logger)
    result:
      "*": "str | plugin(pii_scanner)"

  # Catch-all — default plugins for all tools
  - tool: "*"
    policy:
      - plugin(rate_limiter)
      - plugin(audit_logger)
```
