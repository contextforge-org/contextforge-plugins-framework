# APL: Attribute Policy Language Specification

**Status**: v1.0

APL is an attribute policy language with effects and sequencing support that can be used to control AI application behaviors. It combines attribute-based access control, delegation chain tracking, data sensitivity propagation (taint), field-level transforms, runtime plugins, and external PDP federation into a single predicate-and-effects model. Predicates evaluate over identity, delegation, session, and content attributes. Effects deny requests, label data flows, transform fields, or invoke host-defined logic. Policies are compiled once and evaluated in microseconds.

## 1. Policy Structure

A policy file is a YAML document with two top-level blocks:

```yaml
global:       # Global configuration (policy, defaults, delegation, identity, session)
routes:       # Per-tool/resource/prompt/LLM rules
```

Both blocks are optional. An empty file is a valid (permissive) policy.

### 1.1 global

The `global` block contains everything that applies across all routes: named policy groups (including the reserved `all` baseline), delegation settings, content-type defaults, and infrastructure configuration.

```yaml
global:
  policies:
    all:
      description: "Baseline rules applied to every request"
      policy:
        - require(authenticated)
      post_policy:
        - result.record_count > 1000: deny
    pii:
      description: "PII data access controls"
      metadata:
        owner: security-team
        severity: high
      policy:
        - require(perm.pii_access)
      post_policy:
        - result.ssn != null: taint(PII, session)
    sensitive:
      description: "Sensitive data with delegation restrictions"
      metadata:
        owner: compliance-team
        severity: critical
      policy:
        - require(perm.sensitive_access)
        - delegation.depth > 2: deny

  delegation:
    max_depth: 3
    permission_reduction: monotonic
    chain_audit: always

  defaults:
    tool:
      policy:
        - require(perm.tool_execute)
    resource:
      policy:
        - require(perm.resource_read)
    prompt:
      policy:
        - require(authenticated)

  # Infrastructure (used by the runtime, not the policy engine)
  identity:
    provider: cedarling
  session:
    store: memory
    ttl: 3600
```

| Field | Type | Description |
|---|---|---|
| `policies` | map | Named policy groups. The reserved name `all` is applied to every request unconditionally. Other groups are inherited by routes via `meta.tags`. Each group has `policy` (rules), optional `description`, and optional `metadata` (key-value pairs for tooling/audit). |
| `delegation.max_depth` | integer | Maximum delegation chain depth (parsed, not yet enforced by evaluator) |
| `delegation.permission_reduction` | string | Scope narrowing mode (parsed, not yet enforced) |
| `delegation.chain_audit` | string | Audit mode for delegation (parsed, not yet enforced) |
| `defaults` | map | Per-content-type fallback rules. Keys are `tool`, `resource`, `prompt`, `llm` |
| `identity` | map | Identity resolution configuration (runtime) |
| `session` | map | Session store configuration (runtime) |

### 1.2 routes

An ordered list of route entries. Each route matches one content type and defines its policy, args validation, and result transforms.

```yaml
routes:
  - tool: get_compensation
    meta:
      tags: [pii]
    taint:
      session: [PII]
      message: [contains_compensation]
    plugins:
      rate_limiter:
        max_requests: 10
    args:
      employee_id: "str"
      include_ssn: "bool"
    policy:
      - delegation.depth > 1 & args.include_ssn == true: deny
      - args.include_ssn == true & !perm.view_ssn: deny
      - plugin(rate_limiter)
      - nemo(args.query):
          config_id: "prompt-injection"
          on_match: deny
    result:
      ssn: "str | taint(PII, session) | redact(!perm.view_ssn)"
      salary: "int | redact(!role.hr)"
      internal_notes: "omit"
      employee_id: "str | mask(4)"
      notes: "str | plugin(pii_scanner)"
```

| Field | Type | Description |
|---|---|---|
| `tool` | string | Exact tool name match |
| `resource` | string | Resource URI pattern (`*` suffix for prefix match) |
| `prompt` | string | Exact prompt name match |
| `llm` | string | Exact LLM model name match |
| `tags` | string[] | Tag names to inherit policy from |
| `taint` | string[] or map | Syntactic sugar for unconditional label effects on route invocation. List = session only. Map with `session:`/`message:` = explicit scopes (see Section 4.6) |
| `plugins` | map | Route-level plugin config overrides (see Unified Configuration) |
| `policy` | rule[] | Pre-invoke policy rules — evaluated before the tool call (see Section 2) |
| `args` | map[string, string] | Pipe chains for argument validation/transforms (see Section 4) |
| `result` | map[string, string] | Pipe chains for result transforms (see Section 4) |
| `post_policy` | rule[] | Post-invoke policy rules — evaluated after the tool returns and result transforms run. Can deny based on actual results. Same rule syntax as `policy`. |

Exactly one of `tool`, `resource`, `prompt`, or `llm` should be specified per route.

### 1.3 Evaluation Order

Evaluation happens in two phases — pre-invoke and post-invoke — separated by the actual tool/resource/prompt call.

**Pre-invoke** (evaluated before the tool call):

```
1. global.policies.all.policy                    (always; reserved baseline)
2. global.defaults.{content_type}.policy         (if present)
3. global.policies.{tag}.policy                  (for each tag in route.meta.tags)
4. route.policy
5. route.args pipe chains                        (argument validation/transforms)
```

If any pre-invoke rule denies, the tool call is never made.

**Tool execution** (only if pre-invoke allows)

**Post-invoke** (evaluated after the tool returns):

```
6. route.result pipe chains                      (field transforms: redact, mask, omit, taint)
7. global.policies.all.post_policy               (always; reserved baseline)
8. global.defaults.{content_type}.post_policy    (if present)
9. global.policies.{tag}.post_policy             (for each tag in route.meta.tags)
10. route.post_policy
```

Within each group, rules are evaluated top-to-bottom. The first `deny` stops evaluation. If all rules pass, the result is `allow`.

`post_policy` uses the same rule syntax as `policy` and has access to all the same attribute namespaces (subject, delegation, session, meta) plus the `result.*` namespace populated from the tool's actual response. This enables decisions based on what the tool returned — not just what was requested.

Result transforms (step 6) run before post-policy (steps 7-10) so that `post_policy` rules see the transformed result. For example, if a field was redacted, `post_policy` sees the redacted value.

### 1.4 Core Semantics

APL is a predicate-and-effects language.

Each rule consists of:

- a **predicate** (`when`) evaluated against the current execution context; and
- one or more **effects** (`do`) executed when the predicate matches.

Effects are evaluated in order. Effects may:

- change the decision state (`deny`, `allow`)
- add labels (`taint`)
- modify content (`redact`, `mask`, `omit`, `hash`)
- invoke host-defined logic (`plugin`, external PDP calls)

APL surface forms (the `condition: effect` shorthand, pipe chains, route-level `taint:`, and `on_deny` / `on_allow` reaction blocks) are syntactic conveniences over this common execution model.

A `deny` effect is terminal: evaluation halts immediately. Effects that precede `deny` in the same ordered block are preserved (e.g., a `taint` before `deny` persists for audit).



## 2. Predicate Language

Rules consist of a **predicate** and one or more **effects**. Unconditional rules can omit the predicate entirely.

A bare effect in a policy list is unconditional:

```yaml
- plugin(rate_limiter)
- taint(PII, session)
```

The shorthand form separates the predicate and effect with `:`:

```yaml
- delegation.depth > 2: deny
- args.include_ssn == true: taint(SSN_REQUESTED)
```

The canonical structured form uses explicit `when:` / `do:` keys:

```yaml
- when: delegation.depth > 2
  do: deny

- when: args.include_ssn == true
  do:
    - taint(SSN_REQUESTED)
    - plugin(audit_logger)
```

All forms are equivalent. Bare effects and shorthand are preferred for simple rules. The structured form is preferred for multi-effect rules, tooling, and linting.

If no effect is specified, the default is `deny`.

### 2.1 require()

Shorthand for "deny if this isn't true."

```yaml
# Single attribute (must be truthy)
- require(authenticated)

# All must be truthy (AND, comma-separated)
- require(perm.view_ssn, role.hr)

# At least one must be truthy (OR, pipe-separated)
- require(role.finance | role.admin)
```

`require(X)` compiles to: if NOT X, then deny.

### 2.2 exists()

Checks whether an attribute key is present in the AttributeBag. Returns `true` if the key exists, regardless of its value (`null`, `false`, `0`, empty string all return `true`). Returns `false` only if the key is not present at all.

```yaml
# Check if tool returned an SSN field before acting on it
- exists(result.ssn): taint(PII, session)

# Combine existence check with value check
- exists(result.record_count) & result.record_count > 1000: deny

# Guard against missing args
- exists(args.include_ssn) & args.include_ssn & !perm.view_ssn: deny
```

`exists()` is particularly important in `post_policy` rules where different tools return different fields. Without it, a missing field evaluates as `false` (see Section 2.6), which can cause unexpected behavior with negation (`!result.field` would fire on missing fields).

| Expression | Key missing | Key = `null` | Key = `false` | Key = `"hello"` |
|---|---|---|---|---|
| `exists(key)` | `false` | `true` | `true` | `true` |
| `key` (truthiness) | `false` | `false` | `false` | `true` |
| `!key` | `true` | `true` | `true` | `false` |

### 2.3 Comparison Operators

```yaml
- delegation.depth > 2: deny
- intent.confidence < 0.7: deny
- delegation.depth >= 3: deny
- session.tool_calls <= 10: allow
- subject.type == "agent": deny
- status != "active": deny
```

| Operator | Syntax | Types |
|---|---|---|
| Greater than | `>` | int, float |
| Less than | `<` | int, float |
| Greater or equal | `>=` | int, float |
| Less or equal | `<=` | int, float |
| Equal | `==` | int, float, string, bool |
| Not equal | `!=` | int, float, string, bool |

### 2.4 Set Operators

```yaml
# Set contains value
- session.labels contains "PII": deny

# Value in set
- subject.type in allowed_types: allow

# Value not in set
- status not in active_statuses: deny
```

| Operator | Syntax | Description |
|---|---|---|
| `contains` | `set_key contains "value"` | StringSet attribute contains the literal |
| `in` | `value_key in set_key` | Value is a member of a set |
| `not in` | `value_key not in set_key` | Value is not a member of a set |

### 2.5 Logical Combinators

```yaml
# AND (higher precedence): both must be true
- delegation.depth > 1 & args.include_ssn == true: deny

# OR (lower precedence): either suffices
- role.finance | role.admin: allow

# NOT: negate a boolean
- !authenticated: deny
- !perm.view_ssn: deny
```

| Combinator | Syntax | Precedence |
|---|---|---|
| Grouping | `(expr)` | Highest |
| NOT | `!key` | High |
| AND | `expr1 & expr2` | Middle |
| OR | `expr1 \| expr2` | Lowest |

Parentheses override default precedence:

```yaml
- (role.finance | role.admin) & !delegated: allow
- !(delegated & delegation.depth > 1): allow
```

AND and OR require spaces around the operator: ` & ` and ` | `.

### 2.6 Boolean Truthiness

A bare identifier evaluates as a boolean lookup in the AttributeBag:

```yaml
- authenticated       # bag["authenticated"] == true
- delegated           # bag["delegated"] == true
- args.include_ssn    # bag["args.include_ssn"] == true
```

If the key is missing from the bag, it evaluates as `false`.

### 2.7 Literals

| Type | Syntax | Examples |
|---|---|---|
| String | `"value"` or `'value'` | `"PII"`, `'agent'` |
| Boolean | `true`, `false` | `args.include_ssn == true` |
| Integer | digits, optional sign | `42`, `-100`, `0` |
| Float | digits with `.` | `0.7`, `3.14` |

Unquoted non-numeric values are treated as attribute names (for `in`/`not in` operators).



## 3. Effects

Effects determine what happens when a rule's predicate matches.

APL defines four effect classes:

- **Control effects** (`deny`, `allow`): change the decision state.
- **Label effects** (`taint(...)`): add labels to session or message scope.
- **Content effects** (`redact`, `mask`, `omit`, `hash`): modify field values.
- **Host effects** (`plugin(...)`, external PDP calls): invoke host-defined logic.

```yaml
# Control: deny unconditionally
- !authenticated: deny

# Control: deny with no effect specified (default is deny)
- !authenticated

# Control: explicit allow
- role.admin: allow

# Label: taint the session
- args.include_ssn == true: taint(SSN_REQUESTED)
- delegation.depth > 1: taint(delegated, [session, message])

# Host: invoke a plugin as a decision point
- plugin(rate_limiter)
- args.include_ssn == true: plugin(pii_scanner)

# Multiple effects from one predicate (list = sequential)
- !role.hr:
    - plugin(audit_logger)
    - result.salary | redact
    - taint(unauthorized_comp_access)

# Sequential plugin orchestration
- sequential:
    - plugin(rate_limiter)
    - plugin(audit_logger)

# Parallel plugin orchestration
- parallel:
    - plugin(pii_scanner)
    - plugin(nemo_guardrails)

# Conditional parallel
- args.include_ssn == true:
    parallel:
      - plugin(pii_scanner)
      - plugin(nemo_guardrails)
```

| Effect | Syntax | Kind | Behavior |
|---|---|---|---|
| Deny | `deny` | Control | Halt evaluation, deny the request |
| Allow | `allow` | Control | Explicit allow (evaluation continues) |
| Taint | `taint(label)` | Label | Add label to session (default scope) |
| Taint (scoped) | `taint(label, session)`, `taint(label, [session, message])` | Label | Add label to specific scope(s) |
| Plugin | `plugin(name)` | Host | Invoke a plugin (see §4.7 for kind distinctions) |
| Sequential | `sequential: [...]` | — | Run effects in order, stop on first deny |
| Parallel | `parallel: [...]` | — | Run effects concurrently, all must allow |

### 3.1 Unconditional Effects

Unconditional effects have no predicate and always execute. A bare effect in a policy list is unconditional:

```yaml
policy:
  - require(authenticated)
  - plugin(rate_limiter)
  - taint(tool_accessed, session)
```

The shorthand `true:` also remains valid: `true: plugin(audit_logger)`.

Unconditional effects are useful when you want transforms or plugins in the policy block without a separate `args:` or `result:` section. Everything in the policy block runs top to bottom:

```yaml
policy:
  # 1. Check permissions
  - !perm.view_ssn & args.include_ssn == true: deny

  # 2. Always run rate limiter
  - plugin(rate_limiter)

  # 3. Conditionally taint
  - args.include_ssn == true: taint(SSN_REQUESTED)

  # 4. Always mask employee_id in the result
  - result.employee_id | mask(4)

  # 5. Conditionally redact salary
  - !role.hr: result.salary | redact
```

This is an alternative to using separate `args:` / `result:` blocks; the policy block becomes the single source of execution order. Both approaches are valid; use whichever is clearer for your use case.

### 3.2 Rule Entry Formats

APL supports several equivalent rule forms:

```yaml
policy:
  # Bare effect (unconditional, preferred for single unconditional effects)
  - plugin(rate_limiter)
  - taint(PII, session)

  # Shorthand map form (preferred for single-effect conditional rules)
  - delegation.depth > 2: deny

  # Canonical structured form (preferred for multi-effect rules)
  - when: delegation.depth > 2
    do: deny

  # String form (equivalent to map form)
  - "delegation.depth > 2: deny"
```

All forms compile to the same internal representation. The structured form is canonical and extends naturally with optional keys like `id` and composes cleanly with multi-effect blocks.



## 4. Pipe Chains

The `args` and `result` sections define per-field processing pipelines. Each field maps to a pipe chain, a sequence of steps separated by `|`, executed left to right.

```yaml
args:
  card_number: "validate(luhn) | mask(4)"
  amount: "int | 0..1M"
  memo: "len(..500)"

result:
  ssn: "redact(!perm.view_ssn)"
  salary: "redact(!role.hr)"
  employee_id: "mask(4)"
  internal_notes: "omit"
```

### 4.1 Transforms

Transforms modify field values. They run unconditionally unless a condition is specified.

| Transform | Syntax | Description | Example Output |
|---|---|---|---|
| Mask | `mask(N)` | Replace all but last N chars with `*` | `****6789` |
| Redact | `redact` | Replace with `[REDACTED]` | `[REDACTED]` |
| Conditional redact | `redact(!condition)` | Redact only if condition is true | `redact(!perm.view_ssn)` |
| Omit | `omit` | Remove field from output entirely | (field absent) |
| Hash | `hash` | Replace with hash of value | `hash:00a1b2c3d4e5f678` |
| Taint | `taint(label)` | Label the session (see Section 4.6) | (value unchanged) |
| Taint (scoped) | `taint(label, session)`, `taint(label, [session, message])` | Taint specific scope(s) | (value unchanged) |
| Plugin | `plugin(name)` | Invoke a declared plugin as a field transform (see Section 4.7) | (plugin-dependent) |

Conditional redact uses the same expression syntax as rule predicates. The expression is evaluated against the AttributeBag:

```yaml
# Redact SSN unless caller has perm.view_ssn
ssn: "redact(!perm.view_ssn)"

# Redact salary unless caller has role.hr
salary: "redact(!role.hr)"
```

### 4.2 Validators

Validators check a field value and deny the request if validation fails.

| Validator | Syntax | Description |
|---|---|---|
| Type check | `str`, `int`, `bool`, `float` | Value must be the specified type |
| Type check | `email`, `url`, `uuid` | Specialized type checks (parsed, basic impl) |
| Regex | `regex("pattern")` | Value must match the pattern |
| Named validator | `validate(name)` | Named validator (dispatched as regex currently) |
| Length | `len(..N)`, `len(N..M)`, `len(N..)` | String length constraint |
| Numeric range | `N..M`, `..M`, `N..` | Integer value range |
| Enum | `enum(a, b, c)` | Value must be one of the listed values |

### 4.3 Range Syntax

Ranges support open-ended bounds and numeric suffixes:

```
0..100       # min=0, max=100
..500        # max=500 (no minimum)
0..          # min=0 (no maximum)
0..1M        # min=0, max=1,000,000
0..10k       # min=0, max=10,000
```

Suffixes: `k` or `K` = ×1,000; `M` or `m` = ×1,000,000.

### 4.4 Chain Composition

Steps in a chain execute left to right. Validators run before transforms:

```yaml
# Validate type, then validate range
amount: "int | 0..1M"

# Validate pattern, then mask for display
card_number: "validate(luhn) | mask(4)"

# Validate length, then (in future) scan for PII
memo: "len(..500) | pii.redact"
```

### 4.5 Scan Placeholders

These are parsed but not fully implemented. They produce label steps:

| Scan | Syntax | Current Behavior |
|---|---|---|
| PII redact | `pii.redact` | Redact + label as PII |
| PII detect | `pii.detect` | Label only (no actual detection) |
| Injection scan | `injection.scan` | Label only (no actual scanning) |

### 4.6 Taint

Taint labels track data exposure. Labels accumulate monotonically: once applied, they cannot be removed within their scope's lifetime. Policy rules reference taint labels to restrict future operations.

Taint has two scopes:

| Scope | Lifetime | Stored in | Checked via |
|---|---|---|---|
| **Session** (default) | Session TTL (e.g., 3600s) | `SessionStore` | `session.labels contains "PII"` |
| **Message** | Single request | `SecurityExtension.labels` | `message.labels contains "PII"` |

Both are monotonic within their scope: labels can only be added, never removed. Session labels persist across tool calls. Message labels reset per request.

#### Route-Level Taint

Route-level `taint:` is syntactic sugar for unconditional `taint()` effects triggered by route invocation. Shorthand (list) defaults to session scope. Expanded form (map) specifies both scopes:

```yaml
routes:
  # Shorthand: session only (backward compatible)
  - tool: get_compensation
    taint: [PII]

  # Expanded: explicit scopes
  - tool: get_medical_records
    taint:
      session: [PII, HIPAA]
      message: [PII, HIPAA, medical]

  # Message only: not sensitive enough for session taint
  - tool: get_directory
    taint:
      message: [employee_data]
```

If `taint:` is a list, it's equivalent to `taint: { session: [...] }`. If it's a map with `session:` / `message:` keys, each scope is explicit.

The expanded form above is semantically equivalent to:

```yaml
policy:
  - do:
      - taint(PII, session)
      - taint(HIPAA, session)
      - taint(PII, message)
      - taint(HIPAA, message)
      - taint(medical, message)
```

The shorthand exists for readability.

#### Taint Syntax

`taint()` takes a label and an optional scope:

```yaml
taint(PII)                        # label only, default scope (session)
taint(PII, session)               # explicit session
taint(PII, message)               # message only
taint(PII, [session, message])    # both scopes
```

The second argument is always a scope: either a keyword (`session`, `message`) or a list (`[session, message]`). If omitted, the default is `session`.

For conditional taint, use the rule predicate instead of embedding the condition in the taint call:

```yaml
- when: perm.view_ssn
  do: taint(PII, session)
```

#### Field-Level Taint

`taint()` in pipe chains labels the scope without modifying the field value:

```yaml
result:
  ssn: "str | taint(PII) | redact(!perm.view_ssn)"                       # default: session
  ssn: "str | taint(PII, session) | redact(!perm.view_ssn)"              # explicit session
  ssn: "str | taint(PII, message) | redact(!perm.view_ssn)"              # message only
  ssn: "str | taint(PII, [session, message]) | redact(!perm.view_ssn)"   # both
  salary: "int | taint(financial) | redact(!role.hr)"
  department: "str"                                                       # no taint
```

#### Taint as a Policy Effect

`taint()` can appear in policy blocks as an effect alongside `deny`:

```yaml
policy:
  - args.include_ssn == true: taint(SSN_REQUESTED)                          # default: session
  - args.include_ssn == true: taint(SSN_REQUESTED, message)                 # message only
  - delegation.depth > 1: taint(delegated, [session, message])              # both
```

#### Conditional Taint

Taint can be conditional on attributes. Use the rule predicate to gate the taint effect. A common pattern: only taint if the user actually saw the sensitive value (i.e., it was NOT redacted):

```yaml
policy:
  - when: perm.view_ssn
    do: taint(PII, session)
```

Here, the predicate `perm.view_ssn` ensures the `PII` label is applied only if the user has `perm.view_ssn`, because only then do they actually see the SSN value. If the SSN is redacted, no taint is applied since the user never saw the real data.

In pipe chains, taint is unconditional. If you need conditional taint, move the logic to the policy block where `when:` / `do:` is available.

#### Taint vs Transforms

Taint and transforms are independent operations:

| Operation | Effect on field value | Effect on session | Effect on message |
|--|-|-|-|
| `redact(!role.hr)` | `[REDACTED]` if not HR | None | None |
| `taint(PII)` | Unchanged | Labeled `{PII}` | None |
| `taint(PII, message)` | Unchanged | None | Labeled `{PII}` |
| `taint(PII, [session, message])` | Unchanged | Labeled `{PII}` | Labeled `{PII}` |
| `taint(PII) \| redact(!role.hr)` | `[REDACTED]` if not HR | Labeled `{PII}` | None |
| `omit` | Field removed | None | None |

#### How Taint Drives Policy

Once labels are set, they can be referenced in policy rules:

```yaml
routes:
  - tool: get_compensation
    taint:
      session: [PII]
      message: [contains_compensation]
    result:
      salary: "int | redact(!role.hr)"

  - tool: send_email
    policy:
      # Check session labels (persists across calls)
      - session.labels contains "PII": deny

      # Check message labels (this request only)
      - message.labels contains "contains_compensation": deny
```

Session taint flow:
1. User calls `get_compensation` → session labeled `{PII}`
2. User calls `send_email` → pre-invoke loads `session.labels = {PII}`
3. Rule evaluates: `session.labels contains "PII"` → true → denied

Message taint is useful for downstream pipeline control within the same request. Telemetry plugins can check `message.labels` to decide whether to scrub fields before logging.



### 4.7 Plugin

`plugin(name)` invokes a declared plugin as a host effect. The same surface syntax is used in all contexts, but the plugin's payload type and capabilities depend on the phase in which it appears.

#### Plugin Kinds

| Kind | Appears in | Payload | Capabilities |
|---|---|---|---|
| **Field plugin** | pipe chains (`args:` / `result:`) | `FieldPayload` | Modify field value, taint, deny, pass through |
| **Decision plugin** | policy blocks | `MessagePayload` | Allow, deny, taint, modify |
| **Reaction plugin** | `on_deny` / `on_allow` blocks | `MessagePayload` | Taint, audit, alert |

A plugin invoked in an unsupported phase is a validation error.

#### Field Plugins

In pipe chains, plugins act as **modifiers**, like `redact` or `mask`, but with host-defined logic:

```yaml
result:
  notes: "str | plugin(pii_scanner) | redact(!perm.view_notes)"
  body:  "str | plugin(pii_scanner) | nemo(pii-sanitize)"
  card:  "str | plugin(card_validator) | mask(4)"
```

The plugin receives a `FieldPayload`:

```
FieldPayload {
  field_name: "notes"
  field_value: "Performance review pending, do not disclose"
  field_type: "str"
  tool_name: "get_compensation"
  phase: Result
}
```

The plugin can:
- **Modify** the value (return `modified_payload` with updated `field_value`)
- **Taint** (return taint labels to add to session or message)
- **Deny** (return a violation; the entire request is blocked)
- **Pass through** (return unchanged; allow the pipe chain to continue)

#### Decision Plugins

In policy blocks, plugins act as **decision points** over the full request:

```yaml
policy:
  # Single plugin decision
  - plugin(rate_limiter)

  # Conditional plugin invocation
  - when: delegation.depth > 1
    do: plugin(audit_logger)

  # Sequential plugin orchestration
  - sequential:
      - plugin(rate_limiter)
      - plugin(audit_logger)

  # Parallel plugin orchestration
  - parallel:
      - plugin(pii_scanner)
      - plugin(nemo_guardrails)

  # Conditional parallel
  - when: args.include_ssn == true
    do:
      parallel:
        - plugin(pii_scanner)
        - plugin(nemo_guardrails)
```

Decision plugins receive the full `MessagePayload` (not `FieldPayload`) and may allow, deny, or modify the request.

Plugin configuration is declared globally in the `plugins:` section and can be overridden per-route (see Unified Configuration).



## 5. Attribute Namespaces

The predicate language references attributes by dotted names. Attributes are populated from various sources into a flat namespace (the `AttributeBag`).

### 5.1 Subject Attributes

Extracted from `SubjectExtension`:

| Attribute | Type | Source |
|---|---|---|
| `subject.id` | string | Subject identifier |
| `subject.type` | string | `user`, `agent`, `service`, `system` |
| `authenticated` | bool | Always `true` when subject is present |
| `role.{name}` | bool | `true` for each role the subject has |
| `perm.{name}` | bool | `true` for each permission the subject has |
| `subject.roles` | string set | All roles |
| `subject.permissions` | string set | All permissions |
| `subject.teams` | string set | All teams |

### 5.2 Delegation Attributes

Extracted from `DelegationExtension`:

| Attribute | Type | Source |
|---|---|---|
| `delegated` | bool | Whether the request is delegated |
| `delegation.depth` | int | Number of hops in the delegation chain |
| `delegation.age` | float | Seconds since original delegation |
| `delegation.origin` | string | Original subject ID |
| `delegation.actor` | string | Current actor ID |
| `delegation.scopes` | string set | Scopes granted at the current hop |
| `delegation.strategy` | string | Token strategy (`token_exchange`, `ucan`, etc.) |

### 5.3 Authorization Details Attributes (RFC 9396)

Extracted from the latest hop's `authorization_details`:

| Attribute | Type | Source |
|---|---|---|
| `authorization_details.count` | int | Number of detail entries |
| `authorization_details.types` | string set | All `type` values across entries |
| `authorization_details.actions` | string set | All `actions` values across entries |
| `authorization_details.identifiers` | string set | All `identifier` values across entries |
| `authorization_details.locations` | string set | All `locations` values across entries |

### 5.4 Session Attributes

Set by the gateway from session state:

| Attribute | Type | Source |
|---|---|---|
| `session.labels` | string set | Accumulated labels (PII, financial, etc.) |
| `session.tool_calls` | int | Number of tool calls in session |
| `session.cost` | float | Accumulated cost |
| `session.tools_seen` | string set | Tools called in this session |

### 5.5 Meta Attributes

Extracted from `MetaExtension` (host-provided operational metadata about the entity):

| Attribute | Type | Source |
|---|---|---|
| `meta.tags` | string set | Entity tags (e.g., `pii`, `hr`). Drives route matching and policy group inheritance. |
| `meta.scope` | string | Host-defined grouping (virtual server ID, namespace, etc.) |
| `meta.properties.{key}` | string | Arbitrary key-value metadata (e.g., `meta.properties.owner`) |

Meta attributes are immutable, set by the host and static config before the pipeline runs.

### 5.6 Content Attributes

Extracted from the `ContentSurface` with a configurable prefix (`args.` or `result.`):

| Attribute | Type | Source |
|---|---|---|
| `args.{field}` | varies | Tool call argument values |
| `result.{field}` | varies | Tool result field values |

The prefix is set by the pipeline executor: `args.` for pre-invocation, `result.` for post-invocation.



## 6. Pipeline Execution Model

The APL engine compiles each route into a `CompiledPipeline`, an ordered list of steps.

### 6.1 Step Types

| Step | Source | Description |
|---|---|---|
| **Validate** | `args:` / `result:` pipes | Check a field value, deny if invalid |
| **Transform** | `args:` / `result:` pipes | Modify a field value (mask, redact, omit, hash) |
| **Policy** | `policy:` rules | Evaluate a condition, deny if matched |
| **Label** | scan placeholders | Add labels to the content surface |
| **ExternalPdp** | `opa()` / `authzen()` | Delegate to an external PDP (OPA, Cedar, AuthZen) |

### 6.2 Compilation Order

Steps are compiled from the route definition in this order:

```
1. args pipes        (field validation + transforms, pre-execution)
2. policy rules      (policy checks, interleaved)
3. result pipes      (field transforms, post-execution)
```

Within `args` and `result`, fields are processed in YAML iteration order. Within `policy`, rules are processed top to bottom.

### 6.3 Execution

The pipeline executes steps sequentially. At each step:

1. **Validate**: Check the field value. If validation fails → DENY with reason.
2. **Transform**: If predicate is met (or unconditional), modify the field value in place.
3. **Policy**: Evaluate the predicate against the AttributeBag.
   - If predicate matches and effect is `deny` → DENY.
   - If predicate matches and effect is `allow` → continue.
   - If predicate doesn't match → continue.
4. **Label**: Add labels to the content surface.
5. **ExternalPdp**: Call external PDP, incorporate decision.

The pipeline halts on the first DENY. If all steps pass, the result is ALLOW.

#### Ordered Effect Semantics

For each rule with multiple effects:

1. Evaluate the predicate, if present. If it does not match, skip the block.
2. Execute effects left to right (or top to bottom in a list).
3. If a control effect (`deny`) executes, the pipeline halts immediately.
4. Effects executed before `deny` are preserved. A `taint` before `deny` persists for audit.
5. Non-terminal effects accumulate: multiple `taint` effects in the same block all apply.

#### Parallel Block Semantics

For `parallel` blocks:

- All branches observe the same input context (the AttributeBag and ContentSurface at block entry).
- Content mutations from one branch are **not** visible to sibling branches.
- Taint labels from all branches are unioned monotonically. Labels from every branch apply regardless of individual branch outcomes.
- If **any** branch produces `deny`, the overall block denies.
- If all branches allow, the block allows.

### 6.4 Attribute Resolution

When a predicate references an attribute:

1. If the attribute name starts with the surface prefix (`args.` or `result.`), look up the field in the `ContentSurface`.
2. Otherwise, look up the attribute in the `AttributeBag`.

This allows policy rules to reference both identity context and content values:

```yaml
# delegation.depth from the bag, args.include_ssn from the surface
- delegation.depth > 1 & args.include_ssn == true: deny
```



## 7. External PDP Delegation

The APL pipeline can delegate policy decisions to external Policy Decision Points (OPA, Cedar, AuthZen, or custom services). External PDP steps are interleaved with local rules, so organizations can keep their existing policy infrastructure while CPEX adds identity, delegation, session tracking, and data transforms around the external call.

### 7.1 How It Works

An `ExternalPdp` step in the pipeline:
1. Extracts context from the `AttributeBag` and `ContentSurface` (identity, delegation, session, args)
2. Sends it to the external PDP over HTTP
3. Reads the allow/deny response
4. Continues or halts the pipeline based on the result

The Rust core builds the request and consumes the response. The actual HTTP call is handled by the host (Python gateway) via the `PdpResolver` interface, keeping the Rust core synchronous and transport-agnostic.

### 7.2 AuthZen

[OpenID AuthZen](https://openid.net/specs/openid-authzen-authorization-api-1_0.html) defines a standard evaluation API that is PDP-agnostic. The same interface works whether the backend is OPA, Cedar, Topaz, Cerbos, or any other engine.

**Request:**
```json
POST /access/v1/evaluation
{
  "subject": {
    "type": "user",
    "id": "alice@corp.com",
    "properties": {
      "roles": ["hr", "hr_analyst"],
      "delegation_depth": 1
    }
  },
  "action": {
    "name": "read"
  },
  "resource": {
    "type": "tool",
    "id": "get_compensation"
  },
  "context": {
    "session": { "labels": ["PII"], "tool_calls": 3 },
    "authorization_details": { "actions": ["read"], "types": ["tool_invocation"] },
    "delegation": { "depth": 1, "origin": "alice@corp.com" },
    "args": { "employee_id": "EMP-001234" }
  }
}
```

**Response:**
```json
{ "decision": true }
```

The mapping from CPEX types to AuthZen is direct:

| CPEX Source | AuthZen Field |
|---|---|
| `SubjectExtension` | `subject` (type, id, properties) |
| Route content type | `action` (name) |
| Tool / resource name | `resource` (type, id) |
| Delegation, session, authorization_details, args | `context` |

**Python resolver:**
```python
from cpex.framework.pdp import AuthZenResolver

resolver = AuthZenResolver("https://pdp.corp.com/access/v1/evaluation")
result = await resolver.resolve(input_data)
# result.allowed: bool, result.reason: str | None
```

### 7.3 OPA (Open Policy Agent)

OPA uses a free-form input document evaluated against Rego policies.

**Request:**
```json
POST /v1/data/cpex/authz/allow
{
  "input": {
    "subject": { "id": "alice@corp.com", "type": "user", "roles": ["hr"] },
    "delegation": { "depth": 1, "origin": "alice@corp.com" },
    "session": { "labels": ["PII"], "tool_calls": 3 },
    "authorization_details": { "actions": ["read"], "types": ["tool_invocation"] },
    "tool": "get_compensation",
    "action": "read",
    "args": { "employee_id": "EMP-001234" }
  }
}
```

**Response:**
```json
{ "result": true }
```

**Example Rego policy:**
```rego
package cpex.authz

default allow = false

# Allow if tool is in authorized details and delegation is shallow
allow {
    "tool_invocation" in input.authorization_details.types
    "read" in input.authorization_details.actions
    input.delegation.depth <= 3
}

# Block forwarding from PII-tainted sessions
deny[msg] {
    input.session.labels[_] == "PII"
    input.action == "forward"
    msg := "Cannot forward data from PII-tainted session"
}

allow {
    not deny[_]
    # ... other allow rules
}
```

**Python resolver:**
```python
from cpex.framework.pdp import OpaResolver

resolver = OpaResolver("http://opa:8181/v1/data/cpex/authz/allow")
result = await resolver.resolve(input_data)
```

### 7.4 Pipeline Integration

External PDP calls sit alongside local APL rules in the pipeline. Local rules handle fast, structural checks (identity, delegation depth, session labels). External PDPs handle organization-specific policies that may already exist in OPA/Cedar.

```yaml
routes:
  - tool: get_compensation
    policy:
      # Local: fast structural checks
      - require(authenticated)
      - delegation.depth > 3: deny

      # External: OPA rule group with side effects
      - opa("http://opa:8181/v1/data/hr/compensation/deny"):
          on_deny:
            - deny
            - taint(compensation_violation, session)
            - plugin(audit_logger)
          on_allow:
            - taint(compensation_accessed, session)

      # External: Cedar policy via Cedarling
      - cedar:
          action: "Jans::Action::Read"
          resource_type: "Jans::CompensationRecord"
          on_deny:
            - deny
            - plugin(compliance_alert)

      # External: AuthZEN PDP
      - authzen("https://authz.corp.com/access/v1/evaluation"):
          on_deny:
            - deny

      # External: NeMo guardrails on specific field
      - nemo(args.query):
          config_id: "prompt-injection"
          on_deny:
            - deny
            - taint(injection_attempt, [session, message])
            - plugin(security_alert)

      # Local: session-based enforcement
      - session.labels contains "PII": deny
```

### 7.5 Reaction Model: `on_deny` / `on_allow`

External PDP steps support `on_deny` and `on_allow` blocks that define side effects when the PDP returns a decision. This separates the **decision** (made by the external PDP) from the **consequences** (taint, plugins, and deny scoping, handled by APL).

#### Group-Level Reactions

The simplest form reacts to any deny or allow from the PDP:

```yaml
- opa("http://opa:8181/v1/data/hr/compensation/deny"):
    on_deny:
      - deny
      - taint(policy_violation, session)
      - plugin(audit_logger)
    on_allow:
      - taint(compensation_accessed, session)
```

`on_deny` runs when the PDP denies. `on_allow` runs when the PDP allows. Both are lists of APL effects: `deny`, `taint()`, `plugin()`, field transforms, or orchestration blocks.

#### Rule-Level Reactions (OPA / Cedar)

OPA returns which specific deny rules matched. Cedar returns which policy IDs triggered. APL can map individual rules to different side effects:

```yaml
- opa("http://opa:8181/v1/data/hr/compensation/deny"):
    on_deny:
      ssn_blocked:
        - deny
        - taint(SSN_DENIED, session)
      delegation_too_deep:
        - deny
        - taint(delegation_violation, [session, message])
        - plugin(compliance_alert)
      _default:                              # any deny rule not explicitly mapped
        - deny
        - plugin(audit_logger)
    on_allow:
      - taint(compensation_accessed, session)
```

The `_default` key catches any deny rule that isn't explicitly mapped. If `_default` is omitted and an unmapped rule denies, the request is denied with no additional side effects.

#### OPA URL as Rule Group Selector

OPA's Data API is path-based: the URL determines which package/rules are evaluated:

```yaml
policy:
  # Evaluate just the compensation deny rules
  - opa("http://opa:8181/v1/data/hr/compensation/deny"):
      on_deny:
        - deny
        - taint(compensation_violation)

  # Evaluate just the PII deny rules (different side effects)
  - opa("http://opa:8181/v1/data/hr/pii/deny"):
      on_deny:
        - deny
        - taint(PII_VIOLATION, [session, message])
        - plugin(compliance_alert)

  # Evaluate the entire HR package
  - opa("http://opa:8181/v1/data/hr/deny"):
      on_deny:
        - deny
        - plugin(audit_logger)
```

This allows fine-grained mapping between OPA rule groups and APL side effects, without modifying the Rego policies themselves.

#### Rule ID Sources by PDP

| PDP | Rule identifier | Source |
|--|-|--|
| OPA | Rule name in `deny` set | `{"deny": ["ssn_blocked", "delegation_deep"]}` |
| Cedar | Policy ID | Cedar diagnostics with policy IDs |
| AuthZEN | Context reason | `{"decision": false, "context": {"reason": {...}}}` |
| NeMo | Config ID + rails status | `{"status": "blocked", "rails_status": {...}}` |

#### Reactions as Full APL Effects

The `on_deny` and `on_allow` lists support the same effects as policy blocks:

```yaml
on_deny:
  # Simple effects
  - deny
  - taint(label)
  - taint(label, [session, message])
  - plugin(name)

  # Orchestration
  - sequential:
      - plugin(audit_logger)
      - plugin(compliance_alert)

  # Conditional (check additional attributes before reacting)
  - delegation.depth > 1:
      - plugin(escalation_alert)
      - taint(delegated_violation)
```

### 7.6 Configuration

Each external PDP step is configured with:

| Field | Type | Description |
|---|---|---|
| `pdp_type` | `opa`, `cedar`, `authzen`, `nemo`, `custom` | Determines request/response format |
| `endpoint` | string | PDP evaluation URL (or inline for `opa:`, `cedar:`) |
| `input_namespaces` | string[] | Which context to send: `subject`, `delegation`, `session`, `authorization_details`, `args` |
| `static_context` | map | Additional fields in every request (tool name, action) |
| `timeout_ms` | int | HTTP timeout (default 500ms) |
| `on_error` | `deny`, `allow`, `fallback` | Behavior when PDP is unreachable |
| `on_deny` | action list or rule map | Side effects when PDP denies (see Section 7.5) |
| `on_allow` | action list | Side effects when PDP allows (see Section 7.5) |
| `cache_ttl_seconds` | int | Cache decisions for this duration (0 = no cache) |

### 7.7 Failure Modes

| Mode | Behavior | Use Case |
|---|---|---|
| `deny` | PDP unreachable → deny request (fail closed) | Sensitive operations, compliance |
| `allow` | PDP unreachable → allow request (fail open) | Availability-critical paths |
| `fallback` | PDP unreachable → use local fallback rules | Defense in depth |

### 7.7 Implementation Status

- `ExternalPdp` pipeline step type: **implemented** in Rust (`pipeline.rs`)
- `PdpResolver` trait: **implemented** in Rust (host callback interface)
- `build_pdp_input()`: **implemented** (extracts namespaces from AttributeBag + ContentSurface)
- `AuthZenResolver`: **implemented** in Python (`cpex/framework/pdp/authzen.py`)
- `OpaResolver`: **implemented** in Python (`cpex/framework/pdp/opa.py`)
- YAML parser syntax for `opa(...)` / `authzen(...)`: **not yet implemented** (currently configured programmatically)



## 8. Grammar (EBNF)

> Note: The grammar below covers the YAML-parsed DSL. External PDP syntax (`opa(...)`, `authzen(...)`) is not yet parsed from YAML and is configured programmatically via the `ExternalPdpConfig` type.

```ebnf
(* Top-level policy file *)
policy_file    = "global:" global_block
               | "defaults:" defaults_block
               | "policies:" policies_block
               | "routes:" routes_block ;

global_block   = "policies:" policies_block
               , ["delegation:" delegation_config]
               , ["defaults:" defaults_block] ;

delegation_config = ["max_depth:" integer]
                  , ["permission_reduction:" string]
                  , ["chain_audit:" string] ;

defaults_block = { content_type ":" defaults_entry } ;
defaults_entry = ["policy:" rule_list]
               , ["post_policy:" rule_list] ;
content_type   = "tool" | "resource" | "prompt" | "llm" ;

policies_block = { policy_name ":" policy_group } ;
policy_group   = ["policy:" rule_list]
               , ["post_policy:" rule_list]
               , ["description:" string]
               , ["metadata:" string_map] ;
(* "all" is a reserved policy_name, applied to every request unconditionally *)

meta_block     = ["tags:" string_list]
               , ["scope:" string]
               , ["properties:" string_map] ;

routes_block   = "[" { route_entry } "]" ;
route_entry    = route_match
               , ["meta:" meta_block]
               , ["policy:" rule_list]
               , ["args:" field_pipes]
               , ["result:" field_pipes]
               , ["post_policy:" rule_list] ;

route_match    = "tool:" string
               | "resource:" string
               | "prompt:" string
               | "llm:" string ;

(* Rules *)
rule_list      = string | "[" { rule_entry } "]" ;
rule_entry     = string                            (* "require(authenticated)" *)
               | { condition_str ":" effect_str }  (* shorthand: delegation.depth > 2: deny *)
               | structured_rule ;                 (* canonical: when/do form *)

structured_rule = ["when:" expression]
                , "do:" effect_or_list ;
effect_or_list  = effect_str
                | "[" { effect_str } "]" ;

(* Predicates *)
expression     = or_expr ;
or_expr        = and_expr { " | " and_expr } ;
and_expr       = unary_expr { " & " unary_expr } ;
unary_expr     = "!" unary_expr
               | primary_expr ;

primary_expr   = "(" expression ")"
               | require_fn
               | exists_fn
               | comparison
               | set_op
               | identifier ;

require_fn     = "require(" require_args ")" ;
require_args   = identifier { "," identifier }    (* AND *)
               | identifier { "|" identifier } ;  (* OR *)

exists_fn      = "exists(" identifier ")" ;
(* Returns true if key is present in the AttributeBag, regardless of value *)

comparison     = identifier comp_op literal ;
comp_op        = ">" | "<" | ">=" | "<=" | "==" | "!=" ;

set_op         = identifier "contains" literal
               | identifier "in" identifier
               | identifier "not in" identifier ;

(* Literals *)
literal        = quoted_string | boolean | integer | float | identifier ;
quoted_string  = '"' chars '"' | "'" chars "'" ;
boolean        = "true" | "false" ;
integer        = ["-"] digits ;
float          = ["-"] digits "." digits ;
identifier     = letter { letter | digit | "." | "_" } ;

(* Effects *)
effect_str     = "deny"
               | "allow"
               | "taint(" taint_args ")"
               | "plugin(" identifier ")" ;

taint_args     = identifier                                  (* label only, default scope *)
               | identifier "," scope ;                      (* label + scope *)

scope          = "session"
               | "message"
               | "[" "session" "," "message" "]" ;

(* Pipe chains *)
field_pipes    = { field_name ":" pipe_chain } ;
pipe_chain     = pipe_segment { "|" pipe_segment } ;

pipe_segment   = transform | validator | scan ;

transform      = "mask(" integer ")"
               | "redact"
               | "redact(" expression ")"
               | "omit"
               | "hash" ;

validator      = type_check | range | length | regex | enum_check | named_validator ;
type_check     = "str" | "string" | "int" | "bool" | "float"
               | "email" | "url" | "uuid" ;
range          = [integer] ".." [integer_with_suffix] ;
length         = "len(" [integer] ".." [integer] ")" ;
regex          = "regex(" quoted_string ")" ;
enum_check     = "enum(" identifier { "," identifier } ")" ;
named_validator = "validate(" identifier ")" ;

integer_with_suffix = integer ["k" | "K" | "M" | "m"] ;

scan           = "pii.redact" | "pii.detect" | "injection.scan" ;
```

### 8.1 Surface Sugar and Desugaring

The following constructs are syntactic sugar over the canonical `when:` / `do:` form:

| Surface form | Desugars to |
|---|---|
| `condition: effect` | `when: condition` / `do: effect` |
| Bare `effect` (no predicate) | `do: effect` |
| `true: effect` | `do: effect` |
| Route-level `taint: [X]` | `taint(X, session)` (unconditional) |
| Route-level `taint: { session: [X], message: [Y] }` | `[taint(X, session), taint(Y, message)]` |
| `require(X)` | `when: !X` / `do: deny` |
| `require(X, Y)` | `when: !X \| !Y` / `do: deny` (all must be truthy) |
| `require(X \| Y)` | `when: !(X \| Y)` / `do: deny` (at least one truthy) |

These equivalences are semantic, not merely stylistic: tools MAY normalize policies into canonical structured form internally.



## 9. Complete Example

```yaml
global:
  policies:
    all:
      description: "Baseline authentication requirement"
      policy:
        - require(authenticated)
    pii:
      description: "PII data access controls"
      metadata:
        owner: security-team
        severity: high
      policy:
        - require(perm.pii_access)
      post_policy:
        - exists(result.ssn): taint(PII, session)
    sensitive:
      description: "Sensitive data with delegation restrictions"
      metadata:
        owner: compliance-team
      policy:
        - require(perm.sensitive_access)
        - delegation.depth > 2: deny

  delegation:
    max_depth: 3
    permission_reduction: monotonic

  defaults:
    tool:
      policy:
        - require(perm.tool_execute)
    resource:
      policy:
        - require(perm.resource_read)
    prompt:
      policy:
        - require(authenticated)

  identity:
    provider: cedarling
  session:
    store: memory
    ttl: 3600

routes:
  - tool: get_compensation
    meta:
      tags: [pii]
    taint:
      session: [PII]
      message: [contains_compensation]
    args:
      employee_id: "str"
      include_ssn: "bool"
    policy:
      - delegation.depth > 1 & args.include_ssn == true: deny
      - args.include_ssn == true & !perm.view_ssn: deny
      - plugin(rate_limiter)
      - taint(compensation_accessed, session)
    result:
      ssn: "str | taint(SSN_EXPOSED, session) | redact(!perm.view_ssn)"
      salary: "int | taint(salary_exposed, session) | redact(!role.hr)"
      internal_notes: "omit"
      employee_id: "str | mask(4)"
      notes: "str | plugin(pii_scanner)"
    post_policy:
      - exists(result.ssn) & !perm.view_ssn: deny
      - session.labels contains "PII" & !perm.pii_access: deny

  - tool: send_email
    taint:
      session: [email_attempted]
    policy:
      - require(perm.email_send)
      - session.labels contains "PII": deny
      - message.labels contains "contains_compensation": deny

  - tool: display_compensation
    taint:
      message: [compensation_summary]
    policy:
      - require(perm.tool_execute)

  - resource: "hr://employees/*"
    meta:
      tags: [sensitive]
    policy:
      - require(perm.hr_read)
      - delegated & delegation.depth > 2: deny

  - prompt: summarize_report
    policy:
      - require(perm.report_access)

  - llm: gpt-4o
    policy:
      - session.cost > 5.0: deny
```



## 10. Implementation Notes

### 10.1 Compilation

Policies are compiled once at startup into a `CompiledPolicy` (for the evaluator) or `CompiledPipeline` (for the pipeline executor). No string parsing occurs at evaluation time.

### 10.2 Performance

Typical evaluation: 0.1–0.5ms per request. The evaluator is stateless: the compiled policy is immutable and shared across threads.

### 10.3 Rust Type-Level Guarantees

The APL core is implemented in Rust with the following compile-time guarantees:
- `SubjectExtension` is immutable (no setters)
- `DelegationExtension` chain grows monotonically (append-only API, scope narrowing enforced)
- `AuthorizationDetail` narrowing checked structurally per RFC 9396
- Predicate evaluation is deterministic and side-effect free
- Pipeline execution is deterministic with respect to the compiled pipeline and host/plugin behavior, but produces ordered effects (deny, taint, content transforms, plugin invocations, external PDP delegation)

### 10.4 PyO3 Bindings

All types are exposed to Python via PyO3: `SubjectExtension`, `DelegationExtension`, `DelegationHop`, `AuthorizationDetail`, `AttributeBag`, `PolicyEngine`, `Pipeline`, `ContentSurface`, `TokenIssuer`.
