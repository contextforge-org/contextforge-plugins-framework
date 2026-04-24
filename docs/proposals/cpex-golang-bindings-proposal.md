# CPEX Go Bindings Proposal

**Status**: Draft  
**Date**: April 2026

## Overview

This document describes how Go applications embed the CPEX plugin runtime. The primary model is **Rust plugins with a Go host** — the Go application manages lifecycle and invocation while plugins are written in Rust for maximum performance and security guarantees.

A future extension adds Go-native plugin authoring for custom business logic, with an optimized FFI path that minimizes serialization overhead.

## Why Rust Plugins from Go

### Security guarantees

Rust's ownership model eliminates entire classes of vulnerabilities that are common in Go and C plugins:

- No null pointer dereferences — `Option<T>` enforced at compile time
- No data races — borrow checker prevents concurrent mutation
- No buffer overflows — bounds checking is automatic
- No use-after-free — lifetime tracking at compile time

Plugins handling authentication, PII, and authorization benefit directly from these guarantees. A bug in a Go identity plugin could corrupt memory or leak tokens. In Rust, these bugs don't compile.

#### Immutability and monotonicity guarantees

Beyond memory safety, Rust's type system enforces security invariants that are difficult or impossible to guarantee in Go:

**Immutable payloads.** Payloads are passed to handlers as `&Payload` (shared borrow). A plugin cannot mutate the payload in place — it must explicitly clone and return a modified copy via `PluginResult.ModifiedPayload`. This means:
- Read-only plugins (validators, auditors) never pay for a copy
- The framework always retains the original for audit/rollback
- A malicious or buggy plugin cannot silently alter the payload for downstream plugins

**Monotonic sets (add-only).** Security labels use a `MonotonicSet` — once a label like `"PII"` or `"CONFIDENTIAL"` is added, it cannot be removed. This is enforced at the type level in Rust. In Go, a plugin could simply `delete(labels, "PII")`. In Rust, the `MonotonicSet` type only exposes `insert()` — there is no `remove()` method to call.

**Append-only delegation chains.** The `DelegationExtension` carries an ordered chain of delegation steps. Each step narrows scope — a delegate cannot have more permissions than the delegator. Rust enforces this structurally: the chain type only exposes `push()` with a validation check that the new step's scopes are a subset of the previous step's scopes. Attempting to widen scope is a compile-time or runtime error, not a convention.

**Guarded fields (write tokens).** Certain extension fields use a `Guarded<T>` wrapper that requires a write token to modify. The framework issues write tokens only to plugins that declared the corresponding `write_*` capability. A plugin without the token receives a read-only view — Rust's type system makes it impossible to call the setter without the token. In Go, this would be a convention documented in comments. In Rust, it's enforced by the compiler.

**Pipeline result immutability.** The `PipelineResult` returned by the executor is a separate type from the mutable internal state. The Go host receives it as an immutable struct — policy decisions (allow/deny, violations, modified payload) cannot be tampered with after the executor returns them. Background tasks and context tables are separated into opaque handles precisely so the result itself never needs to be mutable.

#### Capability gating

Extensions are capability-gated per plugin. Each plugin declares what it needs in its config:

```yaml
capabilities:
  - read_security      # can see Subject, Agent, auth_method
  - write_http         # can modify request/response headers
  - read_delegation    # can see the delegation chain
```

The framework filters extensions before dispatch — a plugin that doesn't declare `read_security` receives `nil` for the security extension. It literally cannot access identity data. This is enforced by the runtime, not by convention:

- **Read capabilities** (`read_security`, `read_http`, `read_delegation`, `read_labels`, `read_token`) control visibility. Without the capability, the field is `nil`.
- **Write capabilities** (`write_security`, `write_http`, `append_delegation`, `append_labels`) control mutation. The Rust type system ensures a plugin without the write token cannot modify the field even if it can see it.
- **Meta and Custom** are always visible — no capability required. Meta drives routing; custom is for freeform plugin-to-plugin data.

This enforces least privilege at the framework level. A PII guard plugin can read scopes to make access decisions but cannot read the raw JWT token. An audit logger can see identity for logging but cannot modify headers. Only the token-exchange plugin — which explicitly declares both `read_token` and `write_http` — can read the token and inject the exchanged result into upstream headers.

In Go or Python, capability gating is advisory — a plugin could cast around it or access raw fields. In Rust, the types enforce it. A plugin that receives `FilteredExtensions` with `security: None` has no path to the security data — there is nothing to cast, no unsafe escape hatch, no reflection trick.

### Access to the CPEX plugin ecosystem

By adopting CPEX, Go applications gain access to a growing library of pre-built, tested plugins shared across multiple frameworks:

- **Identity resolver** — JWT validation, SPIFFE, OAuth token introspection
- **PII guard** — field-level PII detection and access control
- **Rate limiter** — per-entity, configurable rate limiting
- **Audit logger** — structured audit trail with async flush
- **Token delegation** — scope-narrowing token exchange chains
- **APL policy evaluator** — attribute-based policy evaluation

These plugins are used in ContextForge (MCP security), Praxis (AI proxy), and other hosts. Bugs found in one deployment are fixed for all. Host applications don't need to rewrite common security patterns — they configure and compose them.

### Performance

Rust plugins run natively in the CPEX executor with zero serialization overhead. The 5-phase pipeline, route resolution, context management, and background task spawning all happen in Rust. The Go host only crosses the FFI boundary once per hook invocation (to call `cpex_invoke` and get back the result), not once per plugin.

## Architecture: Rust Plugins, Go Host

```
Go Application (host)
│
├── Go Host Layer (cpex-go)
│   ├── Manager wrapper (lifecycle, invoke)
│   ├── Config loading
│   └── Result handling
│
├── C FFI boundary (single crossing per invoke)
│
└── Rust Core (cpex-core)
    ├── Plugin instances (Rust, native)
    ├── 5-phase executor
    ├── Route resolution + caching
    ├── PluginContextTable
    └── BackgroundTasks
```

In this model:

- **Plugins are Rust crates** compiled into a shared library (`.so` / `.dylib`) or statically linked
- **The Go host** calls into Rust via cgo to create the manager, load config, register factories, invoke hooks, and read results
- **Data crosses the boundary once** per invoke call — payload and extensions go in as MessagePack, PipelineResult comes back as MessagePack
- **No per-plugin FFI crossings** — the Rust executor dispatches to Rust plugins natively

### Why MessagePack for the boundary

- **Binary, compact** — ~30% smaller than JSON
- **Built-in to most languages** — Go (`msgpack`), Rust (`rmp-serde`), Python (`msgpack-python`)
- **Fast** — encode/decode is ~1-2us for typical payloads
- **Same format for all bindings** — Go, Python, WASM guests all use the same serialization

### What crosses the boundary

```
Go → Rust (once per invoke):
  hook_name          — string
  payload            — MessagePack bytes (serialized)
  extensions         — MessagePack bytes (serialized)
  context_table      — opaque Rust handle (NOT serialized)

Rust → Go (once per invoke):
  PipelineResult     — MessagePack bytes (continue_processing, modified_payload,
                       modified_extensions, violation, metadata)
  context_table      — opaque Rust handle (NOT serialized)
  background_tasks   — opaque Rust handle (NOT serialized)
```

Key optimization: the `ContextTable` and `BackgroundTasks` never cross the serialization boundary. Go holds opaque handles to Rust-owned objects and passes them back. The Rust side manages the actual data. This avoids serializing potentially large context state on every invocation.

The Rust executor runs all plugins, resolves routes, manages context, and returns the aggregate result. No round-trips per plugin.

## C FFI Surface

These are internal plumbing — Go plugin authors never see them. The Go `PluginManager` wrapper (below) provides the clean API.

```c
// Opaque handles — Go holds these, Rust owns the data
typedef void* CpexManager;
typedef void* CpexContextTable;
typedef void* CpexBackgroundTasks;

// Create a manager from YAML config.
// Factories for built-in plugin kinds are registered internally.
CpexManager cpex_manager_new(const char* config_yaml, int config_len);

// Register an additional plugin factory by kind name.
int cpex_register_builtin_factory(CpexManager mgr, const char* kind, int kind_len);

// Initialize all plugins.
int cpex_initialize(CpexManager mgr);

// Invoke a hook.
// context_table is an opaque handle (NULL for first invocation).
// Returns MessagePack-encoded PipelineResult + opaque handles for
// context_table and background_tasks.
int cpex_invoke(
    CpexManager mgr,
    const char* hook_name, int hook_len,
    const uint8_t* payload_msgpack, int payload_len,
    const uint8_t* extensions_msgpack, int ext_len,
    CpexContextTable context_table,             // opaque, NULL for first call
    uint8_t** result_msgpack_out, int* result_len_out,
    CpexContextTable* context_table_out,        // opaque handle out
    CpexBackgroundTasks* bg_handle_out          // opaque handle out
);

// Wait for background tasks to complete. Returns MessagePack-encoded errors.
int cpex_wait_background(
    CpexBackgroundTasks bg_handle,
    uint8_t** errors_msgpack_out, int* errors_len_out
);

// Free resources
void cpex_free_bytes(uint8_t* ptr, int len);
void cpex_free_context_table(CpexContextTable ct);
void cpex_free_background(CpexBackgroundTasks bg);
void cpex_shutdown(CpexManager mgr);
void cpex_manager_free(CpexManager mgr);
```

## Go SDK Types

### PluginManager

The `PluginManager` is the primary API. It wraps all C FFI calls behind
a clean Go interface. Plugin authors and host applications interact only
with this type — never with raw C pointers or MessagePack encoding.

```go
package cpex

import "unsafe"

// PluginManager manages the lifecycle of CPEX plugins and hook dispatch.
// Wraps the Rust PluginManager — all plugin execution happens in Rust.
//
// Usage:
//   mgr, err := NewPluginManager(yamlConfig)
//   mgr.Initialize()
//   result, bg, _ := mgr.InvokeByName("tool_pre_invoke", payload, ext, nil)
//   bg.Wait()
//   mgr.Shutdown()
type PluginManager struct {
    handle unsafe.Pointer // opaque pointer to Rust PluginManager
}

// NewPluginManager creates a manager from a YAML config string.
// Built-in Rust plugin factories (identity, pii, audit, etc.) are
// registered automatically.
func NewPluginManager(yaml string) (*PluginManager, error) { ... }

// NewPluginManagerFromFile creates a manager from a YAML config file.
func NewPluginManagerFromFile(path string) (*PluginManager, error) { ... }

// Initialize calls Initialize on all registered plugins.
// Must be called before invoking any hooks.
func (m *PluginManager) Initialize() error { ... }

// Shutdown gracefully shuts down all plugins and releases resources.
// Calls Shutdown on each plugin, then frees the Rust manager.
func (m *PluginManager) Shutdown() { ... }

// InvokeByName invokes a hook by name with a type-erased payload.
// Payload and extensions are serialized to MessagePack internally.
// The ContextTable is an opaque handle — pass nil on the first call,
// then thread result.ContextTable into subsequent calls.
func (m *PluginManager) InvokeByName(
    hookName string,
    payload any,
    extensions *Extensions,
    contextTable *ContextTable,
) (*PipelineResult, *BackgroundTasks, error) { ... }

// Invoke is the typed invoke path. Deserializes the result payload
// into the concrete type P.
func Invoke[P any](
    m *PluginManager,
    hookName string,
    payload *P,
    extensions *Extensions,
    contextTable *ContextTable,
) (*TypedPipelineResult[P], *BackgroundTasks, error) { ... }

// HasHooksFor returns true if any plugins are registered for the hook.
func (m *PluginManager) HasHooksFor(hookName string) bool { ... }

// PluginCount returns the number of registered plugins.
func (m *PluginManager) PluginCount() int { ... }
```

### Extensions and MetaExtension

These are serialized Go types that cross the MessagePack boundary.
Extensions are passed **separately from the payload** and are
capability-gated per plugin — each plugin only sees the extensions
it has declared capabilities for.

```go
// Extensions carries all extension data alongside the payload.
// The host populates this before invoking hooks. Plugins receive
// a FilteredExtensions view based on their declared capabilities.
type Extensions struct {
    Meta       *MetaExtension       `msgpack:"meta,omitempty"`
    Security   *SecurityExtension   `msgpack:"security,omitempty"`
    Delegation *DelegationExtension `msgpack:"delegation,omitempty"`
    Http       *HttpExtension       `msgpack:"http,omitempty"`
    Labels     map[string]bool      `msgpack:"labels,omitempty"`
    Custom     map[string]any       `msgpack:"custom,omitempty"`
}
```

#### MetaExtension

Operational metadata for route resolution and entity identification.
Always available to all plugins (no capability required).

```go
type MetaExtension struct {
    EntityType string            `msgpack:"entity_type,omitempty"` // "tool", "resource", "prompt", "llm"
    EntityName string            `msgpack:"entity_name,omitempty"` // "get_compensation", "hr://employees/*"
    Tags       []string          `msgpack:"tags,omitempty"`        // drive policy group inheritance
    Scope      string            `msgpack:"scope,omitempty"`       // host-defined grouping (tenant, namespace)
    Properties map[string]string `msgpack:"properties,omitempty"`  // arbitrary key-value metadata
}
```

#### SecurityExtension

Identity and authentication data. Capability-gated: only plugins
with `read_security` see this. Only the identity plugin (with
`write_security`) can populate it.

```go
type SecurityExtension struct {
    // Subject — the authenticated caller
    Subject    *Subject         `msgpack:"subject,omitempty"`

    // Agent — the workload identity of this agent/service
    Agent      *AgentIdentity   `msgpack:"agent,omitempty"`

    // Authentication method used (e.g., "jwt", "mtls", "api_key")
    AuthMethod string           `msgpack:"auth_method,omitempty"`

    // Raw token (opaque to most plugins, used by token exchange)
    // Requires `read_token` capability
    Token      string           `msgpack:"token,omitempty"`
}

// Subject represents the authenticated caller's identity.
// Populated by the identity-resolver plugin from JWT claims
// or other authentication mechanisms.
type Subject struct {
    // Core identity fields
    Issuer    string   `msgpack:"issuer,omitempty"`    // JWT iss
    SubjectID string   `msgpack:"subject_id,omitempty"` // JWT sub
    Audience  []string `msgpack:"audience,omitempty"`   // JWT aud
    ClientID  string   `msgpack:"client_id,omitempty"`  // OAuth client_id

    // Authorization
    Scopes    []string `msgpack:"scopes,omitempty"`     // OAuth scopes
    Roles     []string `msgpack:"roles,omitempty"`      // role-based access

    // Claims — full set of validated JWT claims
    Claims    map[string]any `msgpack:"claims,omitempty"`
}

// AgentIdentity represents this agent's own workload identity.
// Populated by the host before the pipeline runs.
type AgentIdentity struct {
    ClientID    string `msgpack:"client_id"`              // OAuth client_id
    WorkloadID  string `msgpack:"workload_id,omitempty"`  // SPIFFE URI, k8s SA, etc.
    TrustDomain string `msgpack:"trust_domain,omitempty"` // trust domain of workload identity
}
```

#### DelegationExtension

Token delegation chain for multi-agent scenarios. Append-only —
each delegation step narrows scope. Capability-gated: `read_delegation`
to see the chain, `append_delegation` to append.

```go
type DelegationExtension struct {
    // Ordered delegation chain — each step narrows scope
    Chain []DelegationStep `msgpack:"chain,omitempty"`
}

type DelegationStep struct {
    // Who delegated
    Delegator  string   `msgpack:"delegator"`            // subject ID of the delegator
    // Who received the delegation
    Delegate   string   `msgpack:"delegate"`             // subject ID of the delegate
    // Narrowed scopes (must be subset of delegator's scopes)
    Scopes     []string `msgpack:"scopes,omitempty"`
    // When this delegation was created
    IssuedAt   int64    `msgpack:"issued_at,omitempty"`  // Unix timestamp
    // When this delegation expires
    ExpiresAt  int64    `msgpack:"expires_at,omitempty"` // Unix timestamp
    // Constraints on what the delegate can do
    Constraints map[string]any `msgpack:"constraints,omitempty"`
}
```

#### HttpExtension

HTTP request/response metadata for proxy use cases. Carries headers,
method, path without requiring the plugin to parse raw HTTP.
Capability-gated: `read_http` to see, `write_http` to modify headers.

```go
type HttpExtension struct {
    // Request metadata (populated by host before pipeline)
    Method      string      `msgpack:"method,omitempty"`       // GET, POST, etc.
    Path        string      `msgpack:"path,omitempty"`         // request path
    Host        string      `msgpack:"host,omitempty"`         // target host
    Scheme      string      `msgpack:"scheme,omitempty"`       // http or https
    RequestHeaders  map[string][]string `msgpack:"request_headers,omitempty"`

    // Response metadata (populated after upstream responds)
    StatusCode      int                 `msgpack:"status_code,omitempty"`
    ResponseHeaders map[string][]string `msgpack:"response_headers,omitempty"`
}
```

#### Labels (Monotonic Set)

Security labels are add-only. Once a label is set, it cannot be
removed. Plugins with `append_labels` can add labels; all plugins
with `read_labels` can read them. Labels drive policy decisions
(e.g., `"PII"`, `"CONFIDENTIAL"`, `"EXPORT_CONTROLLED"`).

```go
// Labels is a map used as a set — keys are label strings,
// values are always true. Monotonic: add-only.
// Extensions.Labels map[string]bool
```

#### FilteredExtensions

What a plugin actually receives — capability-gated by the framework.
Fields the plugin hasn't declared capabilities for are nil.

```go
type FilteredExtensions struct {
    Meta       *MetaExtension       `msgpack:"meta,omitempty"`       // always visible
    Security   *SecurityExtension   `msgpack:"security,omitempty"`   // requires read_security
    Delegation *DelegationExtension `msgpack:"delegation,omitempty"` // requires read_delegation
    Http       *HttpExtension       `msgpack:"http,omitempty"`       // requires read_http
    Labels     map[string]bool      `msgpack:"labels,omitempty"`     // requires read_labels
    Custom     map[string]any       `msgpack:"custom,omitempty"`     // always visible
}
```

### Capability Gating in Practice

Capability gating is described in [Security guarantees](#security-guarantees)
above. Here is a concrete YAML example showing how different plugins
declare different levels of access:

```yaml
plugins:
  - name: identity-resolver
    kind: builtin/identity
    capabilities:
      - read_http          # reads Authorization header
      - write_security     # populates Subject and Agent
      - read_token         # reads raw token for validation

  - name: pii-guard
    kind: builtin/pii
    capabilities:
      - read_security      # reads Subject.Scopes for clearance check

  - name: token-exchange
    kind: builtin/token-exchange
    capabilities:
      - read_security      # reads current token claims
      - read_delegation    # reads delegation chain
      - append_delegation   # appends delegation step
      - read_token         # reads raw token for exchange
      - write_http         # injects exchanged token into upstream headers

  - name: audit-logger
    kind: builtin/audit
    capabilities:
      - read_security      # logs who made the request
      - read_http          # logs request method/path
```

### ContextTable and BackgroundTasks

These are **opaque handles** to Rust-owned data. They never cross the
serialization boundary — Go just holds a pointer and passes it back.

```go
// ContextTable holds per-plugin context state across hook invocations.
// Opaque handle to Rust-owned data — not serialized.
//
// Pass nil on the first hook call. Thread result.ContextTable into
// subsequent calls to preserve per-plugin local_state and global_state.
type ContextTable struct {
    handle unsafe.Pointer
}

// Close releases the Rust-owned context table.
// Call this when you're done with the request lifecycle.
func (ct *ContextTable) Close() { ... }

// BackgroundTasks holds fire-and-forget task handles.
// Opaque handle to Rust-owned data — not serialized.
type BackgroundTasks struct {
    handle unsafe.Pointer
}

// Wait blocks until all background tasks complete.
// Returns errors from any tasks that panicked.
func (bg *BackgroundTasks) Wait() []PluginError { ... }

// Close releases the task handles without waiting.
// Tasks continue running in the Rust tokio runtime.
func (bg *BackgroundTasks) Close() { ... }
```

### Results

```go
// PipelineResult is the aggregate result from a hook invocation.
// Deserialized from MessagePack returned by the Rust executor.
// The ContextTable is an opaque handle, not part of the MessagePack.
type PipelineResult struct {
    ContinueProcessing bool             `msgpack:"continue_processing"`
    ModifiedPayload    []byte           `msgpack:"modified_payload,omitempty"`
    ModifiedExtensions *Extensions      `msgpack:"modified_extensions,omitempty"`
    Violation          *PluginViolation `msgpack:"violation,omitempty"`
    Metadata           map[string]any   `msgpack:"metadata,omitempty"`
    ContextTable       *ContextTable    // opaque handle, not serialized
}

// IsDenied returns true if the pipeline was halted by a plugin.
func (r *PipelineResult) IsDenied() bool { return !r.ContinueProcessing }

// TypedPipelineResult includes the deserialized payload.
type TypedPipelineResult[P any] struct {
    ContinueProcessing bool
    ModifiedPayload    *P
    ModifiedExtensions *Extensions
    Violation          *PluginViolation
    Metadata           map[string]any
    ContextTable       *ContextTable // opaque handle
}

// IsDenied returns true if the pipeline was halted by a plugin.
func (r *TypedPipelineResult[P]) IsDenied() bool { return !r.ContinueProcessing }
```

### Errors and Violations

```go
type PluginError struct {
    PluginName     string         `msgpack:"plugin_name"`
    Message        string         `msgpack:"message"`
    Code           string         `msgpack:"code,omitempty"`
    Details        map[string]any `msgpack:"details,omitempty"`
    ProtoErrorCode *int64         `msgpack:"proto_error_code,omitempty"`
}

type PluginViolation struct {
    Code           string         `msgpack:"code"`
    Reason         string         `msgpack:"reason"`
    Description    string         `msgpack:"description,omitempty"`
    Details        map[string]any `msgpack:"details,omitempty"`
    PluginName     string         `msgpack:"plugin_name,omitempty"`
    ProtoErrorCode *int64         `msgpack:"proto_error_code,omitempty"`
}
```

## CMF Message Types

CMF (ContextForge Message Format) is the standard payload type for
message-level hooks. It provides a unified representation that works
across LLM providers (OpenAI, Anthropic, Google, etc.) and agentic
frameworks (MCP, A2A). Using CMF means plugins are portable across
any host that speaks CMF.

The Go types mirror the Python CMF model in `models.py`.

```go
// Message is the CMF universal message format.
// Content can be a simple string or a list of multimodal ContentParts.
type Message struct {
    Role       Role            `msgpack:"role"`                  // user, assistant, system, developer, tool
    Content    any             `msgpack:"content"`               // string or []ContentPart
    Channel    *Channel        `msgpack:"channel,omitempty"`     // analysis, commentary, final
    Extensions *Extensions     `msgpack:"extensions,omitempty"`
    Metadata   *Metadata       `msgpack:"metadata,omitempty"`
}

type Role string
const (
    RoleSystem    Role = "system"
    RoleDeveloper Role = "developer"
    RoleUser      Role = "user"
    RoleAssistant Role = "assistant"
    RoleTool      Role = "tool"
)

type Channel string
const (
    ChannelAnalysis   Channel = "analysis"    // internal reasoning
    ChannelCommentary Channel = "commentary"  // tool call preambles
    ChannelFinal      Channel = "final"       // user-facing output
)

// ContentPart is a typed building block of multimodal messages.
// The Type field determines which content struct is populated.
type ContentPart struct {
    Type    ContentType `msgpack:"type"`
    Content any         `msgpack:"content"`  // typed per ContentType
}

type ContentType string
const (
    ContentText       ContentType = "text"
    ContentImage      ContentType = "image"
    ContentThinking   ContentType = "thinking"
    ContentToolCall   ContentType = "tool_call"
    ContentToolResult ContentType = "tool_result"
    ContentResource   ContentType = "resource"
    ContentPrompt     ContentType = "prompt"
    ContentVideo      ContentType = "video"
    ContentAudio      ContentType = "audio"
    ContentDocument   ContentType = "document"
)

// ToolCall represents a tool/function invocation.
type ToolCall struct {
    Name      string         `msgpack:"name"`
    Arguments map[string]any `msgpack:"arguments,omitempty"`
    ID        string         `msgpack:"id,omitempty"`         // correlation with results
    Namespace string         `msgpack:"namespace,omitempty"`  // e.g., "functions"
}

// ToolResult represents the result from a tool execution.
type ToolResult struct {
    ToolCallID string `msgpack:"tool_call_id,omitempty"`
    ToolName   string `msgpack:"tool_name"`
    Content    string `msgpack:"content"`
    IsError    bool   `msgpack:"is_error,omitempty"`
}

// Resource represents external data attached to a message (MCP Resource primitive).
type Resource struct {
    URI          string `msgpack:"uri"`
    Name         string `msgpack:"name,omitempty"`
    Description  string `msgpack:"description,omitempty"`
    ResourceType string `msgpack:"resource_type,omitempty"`
    Content      string `msgpack:"content,omitempty"`  // text content if embedded
    MimeType     string `msgpack:"mime_type,omitempty"`
}

// Metadata carries completion, timing, provenance, and custom metadata.
type Metadata struct {
    Completion *CompletionMetadata `msgpack:"completion,omitempty"`
    Timing     *TimingMetadata     `msgpack:"timing,omitempty"`
    Custom     map[string]any      `msgpack:"custom,omitempty"`
}

type CompletionMetadata struct {
    Model      string `msgpack:"model,omitempty"`
    StopReason string `msgpack:"stop_reason,omitempty"`
    TokensIn   int    `msgpack:"tokens_in,omitempty"`
    TokensOut  int    `msgpack:"tokens_out,omitempty"`
}

type TimingMetadata struct {
    LatencyMs int `msgpack:"latency_ms,omitempty"`
}
```

CMF hooks use the `cmf.` prefix. A single plugin handler can cover
multiple CMF hook points — the framework dispatches based on the
hook name registered in config:

```yaml
hooks:
  - cmf.tool_pre_invoke      # before tool execution
  - cmf.tool_post_invoke     # after tool execution
  - cmf.llm_input            # before LLM call
  - cmf.llm_output           # after LLM response
  - cmf.prompt_pre_fetch     # before prompt retrieval
  - cmf.resource_pre_fetch   # before resource retrieval
```

## Example: Go Host with CMF Plugins

```go
package main

import (
    "log"
    "github.com/contextforge/cpex-go"
)

func main() {
    // --- Startup ---

    // Create the PluginManager from YAML config.
    // Rust plugin factories (identity, pii, audit, etc.) are built in.
    mgr, err := cpex.NewPluginManager(`
plugin_settings:
  routing_enabled: true

global:
  policies:
    all:
      plugins: [identity-resolver]
    pii:
      plugins: [pii-guard]

plugins:
  - name: identity-resolver
    kind: builtin/identity
    hooks: [cmf.tool_pre_invoke, cmf.tool_post_invoke]
    mode: sequential
    priority: 10
    capabilities: [read_http, write_security]

  - name: pii-guard
    kind: builtin/pii
    hooks: [cmf.tool_pre_invoke]
    mode: sequential
    priority: 20
    capabilities: [read_security]

  - name: audit-logger
    kind: builtin/audit
    hooks: [cmf.tool_pre_invoke, cmf.tool_post_invoke]
    mode: fire_and_forget
    priority: 100
    capabilities: [read_security, read_http]

routes:
  - tool: get_compensation
    meta:
      tags: [pii]
    plugins: [audit-logger]
  - tool: "*"
    plugins: [audit-logger]
`)
    if err != nil {
        log.Fatal(err)
    }
    defer mgr.Shutdown()

    if err := mgr.Initialize(); err != nil {
        log.Fatal(err)
    }

    log.Printf("CPEX ready: %d plugins loaded", mgr.PluginCount())

    // --- In the request handler (HTTP middleware, proxy adapter, etc.) ---

    // Build a CMF Message from the incoming request.
    // The message wraps the tool call in the standard CMF format.
    payload := &cpex.Message{
        Role: cpex.RoleAssistant,
        Content: []cpex.ContentPart{
            {
                Type: cpex.ContentToolCall,
                Content: cpex.ToolCall{
                    Name: "get_compensation",
                    ID:   "call_123",
                    Arguments: map[string]any{
                        "employee_id": 42,
                    },
                },
            },
        },
    }

    // Build extensions with routing metadata and HTTP context.
    // The identity-resolver plugin reads the Authorization header;
    // meta drives route resolution to activate the pii policy group.
    ext := &cpex.Extensions{
        Meta: &cpex.MetaExtension{
            EntityType: "tool",
            EntityName: "get_compensation",
        },
        Http: &cpex.HttpExtension{
            Method: "POST",
            Path:   "/mcp/v1",
            Host:   "hr-agent.internal",
            RequestHeaders: map[string][]string{
                "Authorization": {"Bearer eyJ..."},
                "Content-Type":  {"application/json"},
            },
        },
    }

    // Invoke the CMF pre-invoke hook.
    // nil context table — first hook in this request lifecycle.
    result, bg, err := cpex.Invoke[cpex.Message](
        mgr, "cmf.tool_pre_invoke", payload, ext, nil,
    )
    if err != nil {
        log.Fatal(err)
    }

    if result.IsDenied() {
        // Plugin denied — return error to client
        log.Printf("Denied by %s: %s [%s]",
            result.Violation.PluginName,
            result.Violation.Reason,
            result.Violation.Code,
        )
        // Use result.Violation.ProtoErrorCode for the wire response
        // e.g., MCP JSON-RPC error code or HTTP status
        bg.Close()
        result.ContextTable.Close()
        return
    }

    // Forward the request upstream...
    // ...tool executes and returns a result...

    // Build the post-invoke CMF Message with the tool result
    postPayload := &cpex.Message{
        Role: cpex.RoleTool,
        Content: []cpex.ContentPart{
            {
                Type: cpex.ContentToolResult,
                Content: cpex.ToolResult{
                    ToolCallID: "call_123",
                    ToolName:   "get_compensation",
                    Content:    `{"salary": 150000, "currency": "USD"}`,
                },
            },
        },
    }

    // Invoke CMF post-invoke hook.
    // Thread the context table from pre-invoke — preserves plugin state
    // (identity claims, audit trail, etc.).
    postResult, postBg, _ := cpex.Invoke[cpex.Message](
        mgr, "cmf.tool_post_invoke", postPayload, ext, result.ContextTable,
    )
    _ = postResult

    // Wait for all background tasks (audit logger) before responding
    bg.Wait()
    postBg.Wait()

    // Release the context table when the request is done
    postResult.ContextTable.Close()
}
```

## Planned Features

These features are not yet covered in the CPEX core but should be
addressed:

### Reverse-order response hooks

Middleware stacks commonly run response plugins in reverse order — the
last plugin in the request chain sees the response first (onion model).
CPEX currently runs all hooks in priority order regardless of direction.
We plan to support a configurable execution order (forward or reverse)
per hook type, allowing the host to declare that response hooks run in
reverse.

### Required plugins

Host applications may need to enforce that certain plugins must be
present in the pipeline or startup fails (e.g., `jwt-validation` in
inbound, `token-exchange` in outbound). This prevents operators from
accidentally deploying without core security.

CPEX will support this via config:

```yaml
plugin_settings:
  required_plugins:
    - identity-resolver
    - audit-logger
```

Startup validation rejects any config where a required plugin is
missing from the `plugins:` list. The host sets the required list —
plugin authors cannot override it. CPEX already prevents duplicate
plugin names (sealed registry), so a custom plugin cannot replace a
built-in by registering the same name.

### Adapter layer

Host applications often have thin adapters per deployment mode
(ext_proc, HTTP proxy, ext_authz) that convert protocol-specific
types into the shared plugin context. In CPEX terms, the adapter
constructs the CMF `Message` payload and `Extensions` (with
`HttpExtension` for headers, `SecurityExtension` for identity) from
the incoming protocol format. Each adapter is host code — not a CPEX
concern — but the Go SDK should provide helpers for common conversions:

```go
// Adapter helpers (host-level, not CPEX core)
func MessageFromExtProc(req *ext_proc.ProcessingRequest) (*cpex.Message, *cpex.Extensions) { ... }
func MessageFromHTTPRequest(r *http.Request) (*cpex.Message, *cpex.Extensions) { ... }
```

## Future Extension: Go-Native Plugins (Option C)

For custom business logic that host application developers write in Go, a future extension adds Go-native plugin authoring alongside Rust plugins:

```
Manager.InvokeByName()
    │
    ├── Rust plugins (zero-cost, native dispatch)
    │   └── identity-resolver, pii-guard, audit-logger
    │
    └── Go plugins (FFI callback per plugin)
        └── custom-authz, mcp-parser, tool-policy
```

### How it works

The Rust executor knows which plugins are Rust-native and which are Go-hosted. For Go plugins, it calls back into Go via a registered dispatch function. For Rust plugins, it dispatches natively.

### Optimized FFI for Go plugins

The common case — plugin reads payload, returns Allow or Deny without modification — is optimized:

- **Skip result payload serialization** — if `ModifiedPayload` is nil, no payload bytes are returned. Rust keeps the original.
- **Lazy deserialization** — the Go SDK passes raw MessagePack bytes to the handler. Payload is only decoded if the handler accesses it. Audit-only plugins that read metadata from `PluginContext.GlobalState` never deserialize the payload.
- **Shared buffer** — the payload bytes are Rust-owned. Go gets a read-only view via a pointer. Only copied if the plugin modifies.

### Go plugin interface

```go
type HookHandler[P any] interface {
    Handle(payload *P, ext *FilteredExtensions, ctx *PluginContext) *PluginResult[P]
}
```

This is the same interface whether the plugin is Go-native or wrapping a remote call. The Go SDK handles serialization transparently.

### Mixed pipelines

A single pipeline can mix Rust and Go plugins:

```yaml
plugins:
  - name: identity-resolver
    kind: builtin/identity      # Rust
    mode: sequential
    priority: 10

  - name: custom-authz
    kind: go/custom-authz       # Go
    mode: sequential
    priority: 15

  - name: pii-guard
    kind: builtin/pii           # Rust
    mode: sequential
    priority: 20
```

The executor runs identity-resolver natively in Rust, calls back to Go for custom-authz, then runs pii-guard natively in Rust. Only one FFI round-trip for the Go plugin.

## Future Extension: WASM Plugins

WASM plugins provide sandboxed execution for untrusted code. The WASM guest runs in an isolated memory sandbox managed by wasmtime (embedded in the Rust core).

| Approach | Serialization | Sandbox | Performance | Binary Size |
|----------|--------------|---------|-------------|-------------|
| Rust plugins | None | Process-level | ~0 overhead | Small |
| Go plugins (cgo) | MessagePack over FFI | None | ~1-2us per crossing | N/A (compiled in) |
| WASM (Rust guest) | MessagePack over WASM boundary | Full sandbox | ~20-50us per crossing | Small (~100KB) |
| WASM (Go guest) | MessagePack over WASM boundary | Full sandbox | ~20-50us per crossing | Large (~10MB+) |

WASM plugins always have a serialization boundary — the guest has its own linear memory, data must be copied in and out. This is the same whether the guest is Rust, Go, C, or any other language.

For Go-compiled WASM specifically: Go's `wasip1` target produces large binaries and the runtime support is still maturing. Plugin authors targeting WASM would likely write in Rust or C for smaller, faster binaries. Go-in-WASM works but is not the sweet spot.

The Rust core handles WASM dispatch natively — the Go host doesn't need to know whether a plugin is Rust-native or WASM. It's transparent:

```yaml
plugins:
  - name: identity-resolver
    kind: builtin/identity           # Rust native
  - name: custom-scanner
    kind: wasm:///opt/plugins/scan.wasm  # WASM sandbox
  - name: audit-logger
    kind: builtin/audit              # Rust native
```

## Open Questions

1. **Static vs dynamic linking** — should the Rust core be a static library linked into the Go binary, or a shared library (`.so`) loaded at runtime? Static is simpler (single binary), dynamic allows updating CPEX without rebuilding the Go app.

2. **Async/tokio runtime** — the Rust executor uses tokio for async (concurrent plugins, fire-and-forget spawning, timeouts). When Go calls into Rust via cgo, the call runs on a Go-managed thread, not a tokio thread. The Rust side needs its own tokio runtime to execute the async pipeline. Recommendation: one runtime per `PluginManager` instance, created at construction time. Each runtime owns a small thread pool (configurable, default = 4 workers). When Go calls `cpex_invoke`, the Rust side calls `runtime.block_on()` to drive the async pipeline to completion and return the result. This means `cpex_invoke` is a blocking cgo call — Go should call it from a goroutine it's willing to block, which is standard practice for cgo. Fire-and-forget tasks continue running on the tokio runtime's threads after the call returns. A per-manager runtime keeps things isolated — one manager's load doesn't affect another's — and the overhead is negligible for the typical one-manager-per-process deployment.

3. **Memory management** — MessagePack buffers allocated by Rust must be freed by Rust (`cpex_free_bytes`). The Go SDK must ensure these are freed via `runtime.SetFinalizer` or explicit `Close()` methods.

4. **Error propagation** — Rust panics must not propagate across the FFI boundary (undefined behavior). The C API catches panics and returns error codes. The Go SDK translates these to Go errors.

5. **Build integration** — Go projects using CPEX need the Rust toolchain to build the native library. Options: pre-built binaries per platform, or a build script that invokes `cargo build` as part of `go build`.

6. **Plugin API versioning** — how to handle plugins compiled against an older C FFI surface. Semantic versioning of the FFI API, or a version field in the manager handshake.
