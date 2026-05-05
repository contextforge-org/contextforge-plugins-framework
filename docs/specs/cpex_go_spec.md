# CPEX Go — Public API Specification

**Status**: Draft
**Date**: May 2026
**Source**: `github.com/contextforge-org/contextforge-plugins-framework/go/cpex`

CPEX Go is the Golang consumption API for the ContextForge Plugin Extension Framework (CPEX). It embeds the Rust plugin runtime in-process via CGo/FFI, providing Go host systems with a high-performance hook-based extensibility layer. Payloads and extensions cross the FFI boundary as MessagePack bytes; plugin execution happens entirely in the Rust async runtime.

## 1. Architecture

```
┌──────────────────────────────────────────────────────┐
│  Go Host (e.g., AuthBridge)                          │
│                                                      │
│   PluginManager  ───────────────────────────────┐    │
│   │  NewPluginManager[Default]()                │    │
│   │  RegisterFactories(fn)                      │    │
│   │  LoadConfig(yaml)                           │    │
│   │  Initialize()                               │    │
│   │  InvokeByName(hook, payload, ext, ctx)      │    │
│   │  Invoke[P](hook, payload, ext, ctx)         │    │
│   │  HasHooksFor(hook) / PluginCount()          │    │
│   │  Shutdown()                                 │    │
│   └─────────────────────────────────────────────┘    │
│                        │ CGo / MessagePack           │
├────────────────────────┼─────────────────────────────┤
│  libcpex_ffi (Rust)    ▼                             │
│   cpex_manager_new / cpex_invoke / cpex_shutdown     │
│   ┌─────────────────────────────────────────────┐    │
│   │  cpex-core (Rust)                           │    │
│   │  • PluginManager → Executor → Plugins       │    │
│   │  • tokio runtime (async plugin execution)   │    │
│   │  • Phase ordering, capability gating        │    │
│   │  • Route resolution, policy composition     │    │
│   └─────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

**Key design decisions:**

- Plugins are written in Rust (native) and compiled into `libcpex_ffi`. The Go layer is the host embedding API, not a plugin authoring API.
- The FFI boundary uses MessagePack for payloads/extensions and opaque handles for stateful objects (ContextTable, BackgroundTasks).
- Each `PluginManager` owns a dedicated tokio runtime — async plugin execution works from synchronous CGo calls.

## 2. Package & Import

```go
import cpex "github.com/contextforge-org/contextforge-plugins-framework/go/cpex"
```

**Dependencies:**

| Dependency | Purpose |
|---|---|
| `github.com/vmihailenco/msgpack/v5` | MessagePack serialization across FFI |

**Build requirements:**

```bash
# Build the Rust FFI library first
cargo build --release -p cpex-ffi

# Then build/test Go code
go test -v ./...
```

CGo links against `libcpex_ffi` from `target/release/`.

## 3. Lifecycle

```
NewPluginManagerDefault()
        │
        ▼
RegisterFactories(fn)       ← register Rust plugin factories via callback
        │
        ▼
LoadConfig(yaml)            ← YAML with plugin definitions, routing, policies
        │
        ▼
Initialize()                ← instantiate and wire all plugins
        │
        ▼
InvokeByName / Invoke[P]   ← dispatch hooks (repeatable)
        │
        ▼
Shutdown()                  ← graceful teardown
```

## 4. Quick Reference

| Operation | Method |
|---|---|
| Create manager | `NewPluginManagerDefault()` or `NewPluginManager(yaml)` |
| Register factories | `mgr.RegisterFactories(fn)` |
| Load config | `mgr.LoadConfig(yaml)` |
| Initialize | `mgr.Initialize()` |
| Check hooks exist | `mgr.HasHooksFor(hookName)` |
| Count plugins | `mgr.PluginCount()` |
| Invoke (untyped) | `mgr.InvokeByName(hook, type, payload, ext, ctx)` |
| Invoke (typed) | `Invoke[P](mgr, hook, type, payload, ext, ctx)` |
| Check denial | `result.IsDenied()` |
| Get violation | `result.Violation` |
| Thread context | Pass returned `*ContextTable` to next invoke |
| Wait background | `bg.Wait()` |
| Release background | `bg.Close()` |
| Shutdown | `mgr.Shutdown()` |

## 5. Core Types

### 5.1 PluginManager

The top-level object. Owns the Rust runtime and plugin registry.

```go
type PluginManager struct { /* opaque CGo handle */ }

// Construction
func NewPluginManager(yaml string) (*PluginManager, error)
func NewPluginManagerDefault() (*PluginManager, error)

// Factory registration
func (m *PluginManager) RegisterFactories(fn FactoryRegistrar) error

// Configuration
func (m *PluginManager) LoadConfig(yaml string) error

// Initialization
func (m *PluginManager) Initialize() error

// Query
func (m *PluginManager) HasHooksFor(hookName string) bool
func (m *PluginManager) PluginCount() int

// Invocation
func (m *PluginManager) InvokeByName(
    hookName string,
    payloadType uint8,
    payload any,
    extensions *Extensions,
    contextTable *ContextTable,
) (*PipelineResult, *ContextTable, *BackgroundTasks, error)

// Typed invocation (generics)
func Invoke[P any](
    m *PluginManager,
    hookName string,
    payloadType uint8,
    payload P,
    extensions *Extensions,
    contextTable *ContextTable,
) (*TypedPipelineResult[P], *ContextTable, *BackgroundTasks, error)

// Teardown
func (m *PluginManager) Shutdown()
```

**Notes:**
- `NewPluginManager(yaml)` creates the manager AND loads config in one call (factories auto-registered).
- `NewPluginManagerDefault()` creates an empty manager — call `RegisterFactories` then `LoadConfig` separately.
- A Go finalizer calls `Shutdown()` if the caller forgets, but explicit `Shutdown()` is recommended.

### 5.2 FactoryRegistrar

```go
type FactoryRegistrar func(handle unsafe.Pointer) error
```

A callback that receives the raw C manager handle. The caller uses this to invoke their own `extern "C"` factory registration function. This is the bridge for registering custom Rust plugin factories that are compiled into a separate shared library.

**Example:**

```go
/*
#include <stdlib.h>
int my_register_factories(void* mgr);
*/
import "C"

err := mgr.RegisterFactories(func(handle unsafe.Pointer) error {
    rc := C.my_register_factories(handle)
    if rc != 0 {
        return fmt.Errorf("factory registration failed: %d", rc)
    }
    return nil
})
```

### 5.3 ContextTable

```go
type ContextTable struct { /* opaque CGo handle */ }
func (ct *ContextTable) Close()
```

Per-plugin state that persists across hook invocations within a single request. Thread the returned `ContextTable` from one `Invoke` call into the next to maintain plugin-local context.

- Pass `nil` on the first invocation.
- After use, the handle is consumed by the next `Invoke` call (ownership transfers to Rust).
- Call `Close()` to release without further use.

### 5.4 BackgroundTasks

```go
type BackgroundTasks struct { /* opaque CGo handle */ }
func (bg *BackgroundTasks) Wait() []string
func (bg *BackgroundTasks) Close()
```

Handle to fire-and-forget tasks spawned by plugins (e.g., async audit logging). Tasks run in the Rust tokio runtime outside the request's latency budget.

- `Wait()` blocks until all background tasks complete. Returns error strings from any that panicked.
- `Close()` releases the handle without waiting — tasks continue running.

### 5.5 PipelineResult

```go
type PipelineResult struct {
    ContinueProcessing bool
    Violation          *PluginViolation
    Metadata           map[string]any
    PayloadType        uint8
    ModifiedPayload    []byte       // raw MessagePack
    ModifiedExtensions []byte       // raw MessagePack
}

func (r *PipelineResult) IsDenied() bool
func (r *PipelineResult) DeserializeExtensions() (*Extensions, error)
func DeserializePayload[T any](result *PipelineResult) (*T, error)
```

### 5.6 TypedPipelineResult

```go
type TypedPipelineResult[P any] struct {
    ContinueProcessing bool
    Violation          *PluginViolation
    Metadata           map[string]any
    PayloadType        uint8
    ModifiedPayload    *P
    ModifiedExtensions *Extensions
}

func (r *TypedPipelineResult[P]) IsDenied() bool
```

The typed invoke path (`Invoke[P]`) automatically deserializes the modified payload and extensions into concrete Go types.

### 5.7 PluginViolation

```go
type PluginViolation struct {
    Code           string
    Reason         string
    Description    string
    Details        map[string]any
    PluginName     string
    ProtoErrorCode *int64
}
```

Structured denial. `Code` is a machine-readable identifier; `Reason` is a short human-readable explanation.

## 6. Extensions

Extensions carry capability-gated metadata alongside the payload. Each plugin sees only the extensions its declared capabilities grant. Serialized as MessagePack across the FFI boundary.

```go
type Extensions struct {
    Meta       *MetaExtension
    Security   *SecurityExtension
    Http       *HttpExtension
    Delegation *DelegationExtension
    Agent      *AgentExtension
    Request    *RequestExtension
    MCP        *MCPExtension
    Completion *CompletionExtension
    Provenance *ProvenanceExtension
    LLM        *LLMExtension
    Framework  *FrameworkExtension
    Custom     map[string]any
}
```

### 6.1 Extension Types

| Extension | Purpose | Key Fields |
|---|---|---|
| `Meta` | Entity identification for route resolution | `EntityType`, `EntityName`, `Tags`, `Scope`, `Properties` |
| `Security` | Identity, labels, data policies | `Subject`, `Agent`, `Labels`, `Classification`, `AuthMethod`, `Objects`, `Data` |
| `Http` | HTTP request/response context | `RequestHeaders`, `ResponseHeaders` |
| `Delegation` | Token delegation chain | `Chain[]`, `Depth`, `OriginSubjectID`, `ActorSubjectID` |
| `Agent` | Agent execution context | `SessionID`, `ConversationID`, `Turn`, `AgentID` |
| `Request` | Execution environment and tracing | `Environment`, `RequestID`, `TraceID`, `SpanID`, `Timestamp` |
| `MCP` | MCP entity metadata | `Tool`, `Resource`, `Prompt` |
| `Completion` | LLM completion stats | `StopReason`, `Tokens`, `Model`, `LatencyMs` |
| `Provenance` | Origin and message threading | `Source`, `MessageID`, `ParentID` |
| `LLM` | Model identity | `ModelID`, `Provider`, `Capabilities` |
| `Framework` | Agentic framework context | `Framework`, `FrameworkVersion`, `NodeID`, `GraphID` |
| `Custom` | Arbitrary key-value pairs | `map[string]any` |

### 6.2 Security Extension Detail

```go
type SecurityExtension struct {
    Labels         []string
    Classification string
    Subject        *SubjectExtension    // authenticated caller
    Agent          *AgentIdentity       // this agent's workload identity
    AuthMethod     string
    Objects        map[string]ObjectSecurityProfile
    Data           map[string]DataPolicy
}

type SubjectExtension struct {
    ID, SubjectType string
    Roles, Permissions, Teams []string
    Claims map[string]string
}

type AgentIdentity struct {
    ClientID, WorkloadID, TrustDomain string
}
```

### 6.3 Delegation Extension Detail

```go
type DelegationExtension struct {
    Chain           []DelegationHop
    Depth           int
    OriginSubjectID string
    ActorSubjectID  string
    Delegated       bool
    AgeSeconds      float64
}

type DelegationHop struct {
    SubjectID, SubjectType, Audience, Strategy, Timestamp string
    ScopesGranted []string
    TTLSeconds    *uint64
    FromCache     bool
}
```

### 6.4 Capability-Gated Writes (Rust Plugin Side)

The `capabilities` list in a plugin's YAML config controls which extension fields the plugin can read **and** write. The Rust executor translates declared capabilities into write tokens before calling `Plugin::handle`. A plugin that lacks `write_headers`, for example, receives `http_write_token: None` and cannot modify `HttpExtension`.

The Rust write pattern uses COW (copy-on-write) ownership:

```rust
// In Plugin::handle — capability-gated extension modification
let mut owned = extensions.cow_copy();          // clone mutable slots

if let Some(ref token) = owned.http_write_token {  // token present iff capability declared
    if let Some(http) = owned.http.as_mut() {
        let h = http.write(token);
        h.set_response_header("X-Tool-Name", name);
        h.set_response_header("X-CPEX-Processed", "true");
    }
}

PluginResult::modify_extensions(owned)          // emit modified extensions back to Go
```

On the Go side, `result.ModifiedExtensions` (or `typed.ModifiedExtensions`) carries the updated extensions returned by the plugin. The Go caller can deserialize them with `result.DeserializeExtensions()` (see §13.3).

**Rust `PluginResult` constructors:**

| Constructor | What it signals |
|---|---|
| `PluginResult::allow()` | Pass, no changes |
| `PluginResult::deny(violation)` | Halt pipeline, return violation to Go |
| `PluginResult::modify_extensions(owned)` | Pass, return modified extensions |
| `PluginResult::modify_payload(payload)` | Pass, return modified payload |

## 7. Payload Types

### 7.1 Payload Type Registry

CPEX uses a `payloadType` discriminator to tell the Rust core how to deserialize the payload:

| Constant | Value | Payload Type |
|---|---|---|
| `PayloadGeneric` | `0` | `map[string]any` — untyped JSON-like payload |
| `PayloadCMFMessage` | `1` | `MessagePayload` — CMF message |

Hosts define their own payload structs (e.g., `InboundPreValidationPayload`) and serialize them as `PayloadGeneric`. The type ID tells Rust how to deserialize; Go callers choose the ID and matching struct.

### 7.2 Generic Payload

Any `map[string]any` or struct with msgpack tags. Serialized as MessagePack, deserialized in Rust as a `serde_json::Value`.

```go
payload := map[string]any{
    "tool_name": "get_compensation",
    "user":      "alice",
}
result, ct, bg, err := mgr.InvokeByName("tool_pre_invoke", cpex.PayloadGeneric, payload, ext, nil)
```

### 7.3 CMF MessagePayload

The ContextForge Message Format — a typed, multi-part message with schema versioning.

```go
type MessagePayload struct {
    Message Message `msgpack:"message"`
}

type Message struct {
    SchemaVersion string        `msgpack:"schema_version"`
    Role          string        `msgpack:"role"`
    Content       []ContentPart `msgpack:"content"`
    Channel       string        `msgpack:"channel,omitempty"`
}

func NewMessage(role string, content ...ContentPart) Message
```

### 7.4 Content Parts

`ContentPart` is a tagged union discriminated by `content_type`. Custom msgpack encoding produces the same wire format as Rust's `#[serde(tag = "content_type")]`.

| Content Type | Constructor | Data Field |
|---|---|---|
| `text` | `TextContent(s)` | `.Text` |
| `thinking` | `ThinkingContent(s)` | `.Text` |
| `tool_call` | `ToolCallContent(tc)` | `.ToolCallContent` |
| `tool_result` | `ToolResultContent(tr)` | `.ToolResultContent` |
| `resource` | `ResourceContent(r)` | `.ResourceContent` |
| `resource_ref` | `ResourceRefContent(r)` | `.ResourceRefContent` |
| `prompt_request` | `PromptRequestContent(pr)` | `.PromptRequestContent` |
| `prompt_result` | `PromptResultContent(pr)` | `.PromptResultContent` |
| `image` | `ImageContent(img)` | `.ImageContent` |
| `video` | `VideoContent(vid)` | `.VideoContent` |
| `audio` | `AudioContent(aud)` | `.AudioContent` |
| `document` | `DocumentContent(doc)` | `.DocumentContent` |

**Example:**

```go
msg := cpex.MessagePayload{
    Message: cpex.NewMessage("assistant",
        cpex.TextContent("Looking up compensation data"),
        cpex.ToolCallContent(cpex.ToolCall{
            ToolCallID: "tc_001",
            Name:       "get_compensation",
            Arguments:  map[string]any{"employee_id": 42},
        }),
    ),
}

result, ct, bg, err := cpex.Invoke[cpex.MessagePayload](
    mgr, "cmf.tool_pre_invoke", cpex.PayloadCMFMessage, msg, ext, nil,
)
```

## 8. Hook Types (Built-in)

Hooks are open strings — hosts define their own. The following are built into `cpex-core`:

### 8.1 Legacy Hooks (typed payloads)

| Hook Name | Lifecycle Stage |
|---|---|
| `tool_pre_invoke` | Before tool execution |
| `tool_post_invoke` | After tool execution |
| `prompt_pre_fetch` | Before prompt template fetch |
| `prompt_post_fetch` | After prompt template fetch |
| `resource_pre_fetch` | Before resource fetch |
| `resource_post_fetch` | After resource fetch |
| `identity_resolve` | Identity resolution |
| `token_delegate` | Token delegation |

### 8.2 CMF Hooks (MessagePayload)

| Hook Name | Lifecycle Stage |
|---|---|
| `cmf.tool_pre_invoke` | Before tool execution (CMF message) |
| `cmf.tool_post_invoke` | After tool execution (CMF message) |
| `cmf.llm_input` | Before LLM call |
| `cmf.llm_output` | After LLM response |
| `cmf.prompt_pre_fetch` | Before prompt fetch (CMF) |
| `cmf.prompt_post_fetch` | After prompt fetch (CMF) |
| `cmf.resource_pre_fetch` | Before resource fetch (CMF) |
| `cmf.resource_post_fetch` | After resource fetch (CMF) |

### 8.3 Custom Hooks

Hosts register their own hook names. Any string works:

```go
mgr.InvokeByName("inbound.pre_validation", cpex.PayloadGeneric, payload, ext, nil)
mgr.InvokeByName("outbound.pre_exchange", cpex.PayloadGeneric, payload, ext, nil)
```

## 9. Plugin Configuration (YAML)

Plugins are declared in YAML and loaded via `LoadConfig`. The YAML is parsed by the Rust core.

```yaml
plugin_settings:
  routing_enabled: true
  plugin_timeout: 30

global:
  policies:
    all:
      plugins: [identity-checker]
    pii:
      plugins: [pii-guard]

plugins:
  - name: identity-checker
    kind: builtin/identity
    hooks: [tool_pre_invoke, tool_post_invoke]
    mode: sequential
    priority: 10
    on_error: fail

  - name: pii-guard
    kind: builtin/pii
    hooks: [tool_pre_invoke]
    mode: sequential
    priority: 20
    on_error: fail
    capabilities:
      - read_labels
      - read_subject

  - name: audit-logger
    kind: builtin/audit
    hooks: [tool_pre_invoke, tool_post_invoke]
    mode: fire_and_forget
    priority: 100
    on_error: ignore

  - name: header-injector
    kind: builtin/cmf-header-injector
    hooks: [cmf.tool_pre_invoke, cmf.tool_post_invoke]
    mode: sequential
    priority: 50
    on_error: ignore
    capabilities:
      - read_headers
      - write_headers

routes:
  # Tool-specific route — tags applied to all invocations of this tool
  - tool: get_compensation
    meta:
      tags: [pii, hr]
    plugins:
      - audit-logger

  - tool: list_departments
    plugins:
      - audit-logger

  # Wildcard route — applies to all tools not matched above
  - tool: "*"
    plugins:
      - audit-logger
```

### 9.1 Plugin Modes

| Mode | Behavior |
|---|---|
| `sequential` | Serial execution, can block (deny) AND modify payload |
| `transform` | Serial execution, can modify payload but cannot block |
| `audit` | Serial execution, read-only (no modify, no block) |
| `concurrent` | Parallel execution, can block but cannot modify |
| `fire_and_forget` | Background execution, non-blocking, runs after pipeline completes |
| `disabled` | Plugin loaded but not executed |

### 9.2 Error Handling (`on_error`)

| Value | Behavior |
|---|---|
| `fail` | Halt pipeline, propagate error to caller |
| `ignore` | Log error, continue pipeline |
| `disable` | Log error, disable plugin for remaining lifetime, continue |

### 9.3 Plugin Capabilities

The optional `capabilities` list controls which extension fields a plugin can read and write. The Rust executor passes write tokens only for declared capabilities; undeclared extension slots arrive as `None` in the plugin's `handle()` call.

| Capability | Extensions access granted |
|---|---|
| `read_labels` | `SecurityExtension.labels` (read) |
| `read_subject` | `SecurityExtension.subject` (read) |
| `read_headers` | `HttpExtension.request_headers` (read) |
| `write_headers` | `HttpExtension.response_headers` (read + write token) |

Capabilities declared in YAML are enforced at the Rust core level — a plugin cannot write to extensions it did not declare. See §6.4 for the Rust-side write pattern.

### 9.4 Routes

Routes match invocations by tool name and apply additional plugin overrides or tag injection. Evaluated in order; first match wins. The `"*"` wildcard matches any tool not matched by an earlier route.

```yaml
routes:
  # Exact match — injects meta tags for this tool's invocations
  - tool: get_compensation
    meta:
      tags: [pii, hr]
    plugins:
      - audit-logger

  # Exact match — no meta tags
  - tool: list_departments
    plugins:
      - audit-logger

  # Wildcard — catch-all for remaining tools
  - tool: "*"
    plugins:
      - audit-logger
```

The `meta.tags` field under a route entry augments (or sets) the `MetaExtension.Tags` seen by plugins for that tool, enabling tag-based policy groups to trigger without requiring the Go caller to set tags on every invocation.

## 10. Integration Pattern

The canonical integration pattern for a Go host:

```go
package main

import (
    "fmt"
    "os"
    "unsafe"

    cpex "github.com/contextforge-org/contextforge-plugins-framework/go/cpex"
)

/*
// macOS: add -framework CoreFoundation -framework Security
// Linux: -lm -ldl -lpthread are sufficient
#cgo LDFLAGS: -L${SRCDIR}/../../target/release -lmy_plugins_ffi -lm -ldl -lpthread
#include <stdlib.h>
int my_register_factories(void* mgr);
*/
import "C"

func main() {
    // 1. Create manager
    mgr, err := cpex.NewPluginManagerDefault()
    if err != nil {
        panic(err)
    }
    defer mgr.Shutdown()

    // 2. Register custom plugin factories
    if err := mgr.RegisterFactories(func(handle unsafe.Pointer) error {
        if C.my_register_factories(handle) != 0 {
            return fmt.Errorf("factory registration failed")
        }
        return nil
    }); err != nil {
        panic(err)
    }

    // 3. Load configuration
    yaml, err := os.ReadFile("plugins.yaml")
    if err != nil {
        panic(err)
    }
    if err := mgr.LoadConfig(string(yaml)); err != nil {
        panic(err)
    }

    // 4. Initialize plugins
    if err := mgr.Initialize(); err != nil {
        panic(err)
    }

    // 5. Invoke hooks in the request lifecycle
    ext := &cpex.Extensions{
        Meta: &cpex.MetaExtension{
            EntityType: "tool",
            EntityName: "get_compensation",
            Tags:       []string{"pii"},
        },
        Security: &cpex.SecurityExtension{
            Subject: &cpex.SubjectExtension{
                ID:    "user-123",
                Roles: []string{"analyst"},
            },
        },
    }

    result, ct, bg, err := mgr.InvokeByName(
        "tool_pre_invoke", cpex.PayloadGeneric,
        map[string]any{"tool_name": "get_compensation", "user": "alice"},
        ext, nil,
    )
    if err != nil {
        panic(err)
    }

    if result.IsDenied() {
        fmt.Printf("Denied: %s [%s]\n", result.Violation.Reason, result.Violation.Code)
        ct.Close()
        bg.Close()
        return
    }

    // 6. Thread context into post-invoke
    result2, ct2, bg2, err := mgr.InvokeByName(
        "tool_post_invoke", cpex.PayloadGeneric,
        map[string]any{"tool_name": "get_compensation", "result": "..."},
        ext, ct, // pass context from pre-invoke
    )
    if err != nil {
        panic(err)
    }
    _ = result2
    bg.Close()
    bg2.Close()
    ct2.Close()
}
```

## 11. Typed Invoke Pattern

For hosts using CMF messages or custom structs with strong typing:

```go
// Define a custom payload type with msgpack tags
type InboundPreValidationPayload struct {
    Path     string `msgpack:"path"`
    Audience string `msgpack:"audience"`
}

// Invoke with type safety
result, ct, bg, err := cpex.Invoke[InboundPreValidationPayload](
    mgr,
    "inbound.pre_validation",
    cpex.PayloadGeneric,    // serialized as generic msgpack
    InboundPreValidationPayload{Path: "/api/v1/users", Audience: "my-api"},
    ext,
    nil,
)
if err != nil { /* handle */ }

// result.ModifiedPayload is *InboundPreValidationPayload (or nil if unmodified)
if result.ModifiedPayload != nil {
    fmt.Println("Modified audience:", result.ModifiedPayload.Audience)
}
```

## 12. Zero-Cost Guard Pattern

Check for registered plugins before constructing payloads:

```go
if !mgr.HasHooksFor("inbound.pre_validation") {
    // No plugins configured — skip payload construction and FFI overhead
    return handleRequestDirectly(req)
}

// Only build payload and extensions if plugins are registered
payload := buildPreValidationPayload(req)
ext := buildExtensions(req)
result, ct, bg, err := mgr.InvokeByName("inbound.pre_validation", ...)
```

This pattern ensures zero cost when no plugins are configured for a hook point.

## 13. Result Handling

### 13.1 Allow/Deny

```go
result, ct, bg, err := mgr.InvokeByName(...)
if result.IsDenied() {
    // Pipeline halted by a plugin
    v := result.Violation
    return denyResponse(v.Code, v.Reason, v.Description)
}
// Proceed with original or modified payload
```

### 13.2 Modified Payload

```go
// Raw path — manual deserialization
if len(result.ModifiedPayload) > 0 {
    modified, err := cpex.DeserializePayload[MyPayload](result)
    // use modified
}

// Typed path — automatic deserialization
typed, ct, bg, err := cpex.Invoke[MyPayload](mgr, hook, payloadType, payload, ext, nil)
if typed.ModifiedPayload != nil {
    // use typed.ModifiedPayload directly
}
```

### 13.3 Modified Extensions

```go
if len(result.ModifiedExtensions) > 0 {
    ext, _ := result.DeserializeExtensions()
    // Plugins may have enriched Security.Subject, added Labels, etc.
}
```

### 13.4 Background Tasks

```go
// Option A: Wait for background tasks (e.g., at request boundary)
errors := bg.Wait()
for _, e := range errors {
    log.Warn("background task error:", e)
}

// Option B: Fire and forget
bg.Close()
```

### 13.5 Metadata

```go
if result.Metadata != nil {
    // Aggregate metadata from all plugins in the chain
    if decision, ok := result.Metadata["_decision_plugin"]; ok {
        log.Info("decided by:", decision)
    }
}
```

## 14. Serialization

All types use `msgpack` struct tags matching Rust field names for zero-copy serialization across the FFI boundary. The wire format is MessagePack with named fields (`rmp_serde::to_vec_named` on the Rust side).

**Rules:**
- Go struct fields map 1:1 to Rust struct fields via `msgpack:"field_name"` tags.
- Optional fields use `omitempty` — nil/zero values are not serialized.
- `ContentPart` uses custom `EncodeMsgpack`/`DecodeMsgpack` for tagged-union encoding.
- Byte slices (`[]byte`) are serialized as MessagePack binary, not arrays.

## 15. Thread Safety

- `PluginManager` is safe for concurrent use from multiple goroutines. The underlying Rust `PluginManager` uses `RwLock` for the registry and all plugin state.
- `ContextTable` is NOT safe for concurrent use — it represents per-request state that is threaded sequentially through hook invocations.
- `BackgroundTasks` is safe to call `Wait()` or `Close()` from any goroutine, but only once.

## 16. Gaps and Unimplemented Features

The following features exist in the Python CPEX implementation but are not yet exposed in the Go API. These are tracked for future implementation:

| Feature | Python Location | Status in Go |
|---|---|---|
| `invoke_hook_for_plugin(name, hook, payload)` | `manager.py` | Not implemented — no single-plugin invoke |
| `HookPayloadPolicy` (field-level write control) | `manager.py` / `hooks/policies.py` | Handled in Rust core, not configurable from Go |
| `TenantPluginManager` (per-tenant isolation) | `manager.py` | Not implemented — single global manager only |
| Hook Registry query API | `hooks/registry.py` | Not exposed — `HasHooksFor` is the only query |
| Observability provider injection | `manager.py` | Not exposed — observability configured in Rust |
| Plugin conditions (runtime skip) | `manager.py` | Handled in Rust core via YAML config |
| `OnError.DISABLE` runtime status query | `manager.py` | Not exposed |
| `reset()` (reinitialize without restart) | `manager.py` | Not implemented — shutdown and recreate |
| Extensions tier filtering (capability gating) | `extensions/tiers.py` | Handled in Rust core — not exposed |
| gRPC/Unix/MCP external plugin transports | `framework/external/` | Not yet in Rust core |
| Plugin loader with search paths | `loader/` | Rust uses factory registration instead |
| PDP (AuthZen/OPA) integration | `framework/pdp/` | Not yet in Rust core |
| Isolated (subprocess) plugins | `framework/isolated/` | Not yet in Rust core |
| `retry_delay_ms` in result | `models.py` | Not exposed in FFI result |

## 17. Build & Test

```bash
# 1. Build the Rust FFI library
cargo build --release -p cpex-ffi

# 2. Run Go tests (links against libcpex_ffi)
cd go/cpex && go test -v ./...

# 3. Run the demo (requires demo plugin library)
cd examples/go-demo/ffi && cargo build --release
cd examples/go-demo && go run main.go
```

**Platform notes:**
- macOS: link with `-framework CoreFoundation -framework Security`
- Linux: link with `-lm -ldl -lpthread`
- The `#cgo LDFLAGS` directive in `ffi.go` points to `target/release/`

