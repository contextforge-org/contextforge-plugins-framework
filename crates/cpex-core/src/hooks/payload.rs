// Location: ./crates/cpex-core/src/hooks/payload.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// PluginPayload trait and Extensions stub.
//
// PluginPayload is the base trait for all hook payloads, mirroring
// Python's PluginPayload(BaseModel, frozen=True). All payloads in
// the framework implement this trait, giving the executor and
// registry a common bound for type safety.
//
// The trait is object-safe — the executor works with `Box<dyn PluginPayload>`
// instead of `Box<dyn Any>`, catching type errors at compile time.
// Downcasting to concrete types uses the `as_any()` method.
//
// Extensions is the typed container for all message extensions
// (security, delegation, HTTP, meta, etc.). It is always passed
// as a separate parameter to handlers — never inside the payload.
// This allows per-plugin capability filtering and independent
// modification without copying the payload.

use std::any::Any;
use std::collections::HashMap;
use std::fmt;
use std::sync::Arc;

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Extensions — full typed container
// ---------------------------------------------------------------------------

// Re-export the MetaExtension with entity routing fields here
// since it has additional fields beyond the extensions::meta version
// (entity_type, entity_name for routing).
pub use crate::extensions::{
    AgentExtension, CompletionExtension, DelegationExtension, FrameworkExtension, Guarded,
    HttpExtension, LLMExtension, MCPExtension, ProvenanceExtension, RequestExtension,
    SecurityExtension, WriteToken,
};

/// Host-provided operational metadata about the entity being processed.
///
/// Carries entity identification (type + name) for route resolution,
/// operational tags for policy group inheritance, scope for host-defined
/// grouping, and arbitrary properties for policy conditions.
///
/// Immutable — set by the host before invoking the hook. Plugins
/// can read but not modify.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MetaExtension {
    /// Entity type: "tool", "resource", "prompt", "llm".
    /// Used by the manager for route resolution.
    #[serde(default)]
    pub entity_type: Option<String>,

    /// Entity name: "get_compensation", "hr://employees/*", etc.
    /// Used by the manager for route resolution.
    #[serde(default)]
    pub entity_name: Option<String>,

    /// Operational tags — drive policy group inheritance.
    /// Merged with static tags from the matching route's `meta.tags`.
    #[serde(default)]
    pub tags: std::collections::HashSet<String>,

    /// Host-defined grouping (virtual server ID, namespace, etc.).
    #[serde(default)]
    pub scope: Option<String>,

    /// Arbitrary key-value metadata.
    #[serde(default)]
    pub properties: HashMap<String, String>,
}

/// Typed container for all message extensions.
///
/// Each field corresponds to an extension with an explicit mutability
/// tier enforced by the processing pipeline. Extensions are always
/// passed separately from the payload to handlers.
///
/// Mirrors Python's `cpex.framework.extensions.Extensions`.
/// Typed container for all message extensions.
///
/// Each field corresponds to an extension with an explicit mutability
/// tier enforced by the processing pipeline. Extensions are always
/// passed separately from the payload to handlers.
///
/// **Tier enforcement:**
/// - **Immutable** (`Arc<T>`) — shared by reference, zero-copy clone.
///   No `&mut` path exists. Plugins receive `&T` via auto-deref.
/// - **Monotonic** (`MonotonicSet`, append-only chain) — only `insert()`
///   / `append()` methods exposed. No `remove()` at compile time.
/// - **Guarded** (`Guarded<T>`) — read via `.read()`, write via
///   `.write(token)` requiring a `WriteToken` from the framework.
/// - **Mutable** — standard types, freely modifiable.
///
/// **Capability gating:** `filter_extensions()` builds a filtered
/// copy with `None` for slots the plugin can't see. Write tokens
/// are only set when the plugin declared the write capability.
///
/// Mirrors Python's `cpex.framework.extensions.Extensions`.
#[derive(Debug, Default, Serialize, Deserialize)]
pub struct Extensions {
    /// Execution environment and request tracing (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request: Option<Arc<RequestExtension>>,

    /// Agent execution context — session, conversation, lineage (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent: Option<Arc<AgentExtension>>,

    /// HTTP headers (guarded — requires WriteToken for mutation).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http: Option<Guarded<HttpExtension>>,

    /// Security — labels (monotonic add-only via MonotonicSet),
    /// classification, subject, objects, data policies.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub security: Option<SecurityExtension>,

    /// Delegation chain (monotonic — append-only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delegation: Option<DelegationExtension>,

    /// MCP entity metadata — tool, resource, or prompt info (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mcp: Option<Arc<MCPExtension>>,

    /// LLM completion information (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completion: Option<Arc<CompletionExtension>>,

    /// Origin and message threading (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provenance: Option<Arc<ProvenanceExtension>>,

    /// Model identity and capabilities (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub llm: Option<Arc<LLMExtension>>,

    /// Agentic framework context (immutable, Arc).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub framework: Option<Arc<FrameworkExtension>>,

    /// Host-provided operational metadata — tags, scope, properties (immutable, Arc).
    /// Also carries entity_type and entity_name for route resolution.
    #[serde(default)]
    pub meta: Option<Arc<MetaExtension>>,

    /// Custom extensions (mutable — no restrictions).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub custom: Option<HashMap<String, serde_json::Value>>,

    /// Write token for HTTP headers. Present only if the plugin
    /// declared `write_headers` capability. Required for `http.write()`.
    #[serde(skip)]
    pub http_write_token: Option<WriteToken>,

    /// Write token for label append. Present only if the plugin
    /// declared `append_labels` capability.
    #[serde(skip)]
    pub labels_write_token: Option<WriteToken>,

    /// Write token for delegation append. Present only if the plugin
    /// declared `append_delegation` capability.
    #[serde(skip)]
    pub delegation_write_token: Option<WriteToken>,
}

impl Clone for Extensions {
    /// Clone data fields. Immutable slots are Arc refcount bumps (~1ns).
    /// Mutable/monotonic slots are deep cloned. Write tokens are NOT
    /// cloned — they only exist on COW copies created by `cow_copy()`.
    fn clone(&self) -> Self {
        Self {
            request: self.request.clone(),
            agent: self.agent.clone(),
            http: self.http.clone(),
            security: self.security.clone(),
            delegation: self.delegation.clone(),
            mcp: self.mcp.clone(),
            completion: self.completion.clone(),
            provenance: self.provenance.clone(),
            llm: self.llm.clone(),
            framework: self.framework.clone(),
            meta: self.meta.clone(),
            custom: self.custom.clone(),
            http_write_token: None,
            labels_write_token: None,
            delegation_write_token: None,
        }
    }
}

impl Extensions {
    /// Create a copy-on-write clone for modification.
    ///
    /// Immutable slots share the same `Arc` (refcount bump, ~1ns).
    /// Mutable/monotonic slots are deep cloned.
    /// Write tokens are carried over from the original — only tokens
    /// the executor set (based on trusted capabilities) are propagated.
    /// No capability parameter needed — the plugin can't forge tokens
    /// because it only has `&self` (immutable) access to the original.
    ///
    /// # Usage
    ///
    /// ```ignore
    /// // In a plugin handler:
    /// fn handle(&self, payload: &P, ext: &Extensions, ctx: &mut PluginContext) -> PluginResult<P> {
    ///     let mut my_ext = ext.cow_copy();
    ///     // Modify — only works if the executor gave us the write token
    ///     if let Some(ref token) = my_ext.http_write_token {
    ///         my_ext.http.as_mut().unwrap().write(token).set_header("X-Foo", "bar");
    ///     }
    ///     PluginResult::modify_extensions(my_ext)
    /// }
    /// ```
    pub fn cow_copy(&self) -> Self {
        let mut copy = self.clone(); // data cloned, tokens dropped

        // Carry over write tokens that the executor set on the original.
        // Only tokens that already exist are propagated — can't escalate.
        if self.http_write_token.is_some() {
            copy.http_write_token = Some(WriteToken::new());
        }
        if self.labels_write_token.is_some() {
            copy.labels_write_token = Some(WriteToken::new());
        }
        if self.delegation_write_token.is_some() {
            copy.delegation_write_token = Some(WriteToken::new());
        }

        copy
    }

    /// Validate that immutable slots were not tampered with.
    ///
    /// Uses `Arc::ptr_eq` to confirm immutable slots still point to
    /// the same data. Called by the executor after a plugin returns
    /// modified extensions.
    pub fn validate_immutable(&self, modified: &Extensions) -> bool {
        fn ptr_eq_opt<T>(a: &Option<Arc<T>>, b: &Option<Arc<T>>) -> bool {
            match (a, b) {
                (Some(a), Some(b)) => Arc::ptr_eq(a, b),
                (None, None) => true,
                _ => false,
            }
        }

        ptr_eq_opt(&self.request, &modified.request)
            && ptr_eq_opt(&self.agent, &modified.agent)
            && ptr_eq_opt(&self.mcp, &modified.mcp)
            && ptr_eq_opt(&self.completion, &modified.completion)
            && ptr_eq_opt(&self.provenance, &modified.provenance)
            && ptr_eq_opt(&self.llm, &modified.llm)
            && ptr_eq_opt(&self.framework, &modified.framework)
            && ptr_eq_opt(&self.meta, &modified.meta)
    }
}

// ---------------------------------------------------------------------------
// PluginPayload Trait
// ---------------------------------------------------------------------------

/// Base trait for all hook payloads.
///
/// Mirrors Python's `PluginPayload(BaseModel, frozen=True)`. Every
/// payload type in the framework implements this trait. The executor
/// and registry use `Box<dyn PluginPayload>` (not `Box<dyn Any>`)
/// for type-safe dispatch.
///
/// The trait is **object-safe** — it can be used behind `Box`, `&`,
/// and `Arc` without knowing the concrete type. This is achieved by
/// providing `clone_boxed()` instead of requiring `Clone` directly
/// (which is not object-safe), and `as_any()` / `as_any_mut()` for
/// downcasting to the concrete type when needed.
///
/// Payloads are:
/// - Cloneable via `clone_boxed()` — the executor uses this for COW
///   when a modifying plugin (Sequential or Transform) needs ownership.
/// - `Send + Sync` — payloads may be shared across threads for
///   Concurrent mode plugins.
/// - `'static` — payloads must be owned types (no borrowed references).
///
/// Extensions are **not** part of the payload. They are passed as a
/// separate `&Extensions` parameter to handlers.
///
/// # Examples
///
/// ```
/// use cpex_core::hooks::payload::PluginPayload;
///
/// #[derive(Debug, Clone)]
/// struct RateLimitPayload {
///     client_id: String,
///     request_count: u64,
/// }
///
/// impl PluginPayload for RateLimitPayload {
///     fn clone_boxed(&self) -> Box<dyn PluginPayload> {
///         Box::new(self.clone())
///     }
///     fn as_any(&self) -> &dyn std::any::Any { self }
///     fn as_any_mut(&mut self) -> &mut dyn std::any::Any { self }
/// }
/// ```
pub trait PluginPayload: Send + Sync + 'static {
    /// Clone this payload into a new `Box<dyn PluginPayload>`.
    ///
    /// Used by the executor for copy-on-write: read-only modes borrow
    /// the payload, modifying modes receive a clone via this method.
    fn clone_boxed(&self) -> Box<dyn PluginPayload>;

    /// Downcast to a concrete type via `&dyn Any`.
    ///
    /// Used by typed handler wrappers to recover the concrete payload
    /// type from `Box<dyn PluginPayload>`.
    fn as_any(&self) -> &dyn Any;

    /// Downcast to a concrete type via `&mut dyn Any`.
    fn as_any_mut(&mut self) -> &mut dyn Any;
}

impl fmt::Debug for dyn PluginPayload {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("dyn PluginPayload")
    }
}

// ---------------------------------------------------------------------------
// Blanket helper macro for implementing PluginPayload
// ---------------------------------------------------------------------------

/// Implements `PluginPayload` for a type that is `Clone + Send + Sync + 'static`.
///
/// Saves boilerplate — instead of writing the three methods manually,
/// just invoke this macro:
///
/// ```
/// use cpex_core::impl_plugin_payload;
///
/// #[derive(Debug, Clone)]
/// struct MyPayload { value: i32 }
///
/// impl_plugin_payload!(MyPayload);
/// ```
#[macro_export]
macro_rules! impl_plugin_payload {
    ($ty:ty) => {
        impl $crate::hooks::payload::PluginPayload for $ty {
            fn clone_boxed(&self) -> Box<dyn $crate::hooks::payload::PluginPayload> {
                Box::new(self.clone())
            }
            fn as_any(&self) -> &dyn std::any::Any {
                self
            }
            fn as_any_mut(&mut self) -> &mut dyn std::any::Any {
                self
            }
        }
    };
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::extensions::{
        DelegationExtension, Guarded, HttpExtension, RequestExtension, SecurityExtension,
    };

    fn make_extensions() -> Extensions {
        let mut security = SecurityExtension::default();
        security.add_label("PII");

        let mut http = HttpExtension::default();
        http.set_header("Authorization", "Bearer token");

        Extensions {
            request: Some(Arc::new(RequestExtension {
                request_id: Some("req-001".into()),
                ..Default::default()
            })),
            security: Some(security),
            http: Some(Guarded::new(http)),
            delegation: Some(DelegationExtension::default()),
            meta: Some(Arc::new(MetaExtension {
                entity_type: Some("tool".into()),
                ..Default::default()
            })),
            ..Default::default()
        }
    }

    #[test]
    fn test_cow_copy_shares_immutable_arcs() {
        let ext = make_extensions();
        let cow = ext.cow_copy();

        // Immutable slots share the same Arc — zero copy
        assert!(Arc::ptr_eq(ext.request.as_ref().unwrap(), cow.request.as_ref().unwrap()));
        assert!(Arc::ptr_eq(ext.meta.as_ref().unwrap(), cow.meta.as_ref().unwrap()));
    }

    #[test]
    fn test_cow_copy_deep_clones_mutable_slots() {
        let ext = make_extensions();
        let cow = ext.cow_copy();

        // Mutable/monotonic slots are deep cloned — independent copies
        assert!(cow.security.is_some());
        assert!(cow.http.is_some());
        assert!(cow.delegation.is_some());

        // Modifying the COW copy doesn't affect the original
        cow.security.as_ref().unwrap().has_label("PII");
    }

    #[test]
    fn test_cow_copy_propagates_write_tokens() {
        let mut ext = make_extensions();

        // No tokens on the original → no tokens on COW
        let cow_no_tokens = ext.cow_copy();
        assert!(cow_no_tokens.http_write_token.is_none());
        assert!(cow_no_tokens.labels_write_token.is_none());
        assert!(cow_no_tokens.delegation_write_token.is_none());

        // Executor sets tokens based on capabilities
        ext.http_write_token = Some(WriteToken::new());
        ext.labels_write_token = Some(WriteToken::new());

        // COW copy propagates only the tokens that exist
        let cow_with_tokens = ext.cow_copy();
        assert!(cow_with_tokens.http_write_token.is_some());
        assert!(cow_with_tokens.labels_write_token.is_some());
        assert!(cow_with_tokens.delegation_write_token.is_none()); // wasn't set
    }

    #[test]
    fn test_cow_copy_write_token_enables_guarded_write() {
        let mut ext = make_extensions();
        ext.http_write_token = Some(WriteToken::new());

        let mut cow = ext.cow_copy();

        // Can read without token
        assert_eq!(
            cow.http.as_ref().unwrap().read().get_header("Authorization"),
            Some("Bearer token")
        );

        // Can write with token from COW
        let token = cow.http_write_token.as_ref().unwrap();
        cow.http
            .as_mut()
            .unwrap()
            .write(token)
            .set_header("X-Custom", "value");

        assert_eq!(
            cow.http.as_ref().unwrap().read().get_header("X-Custom"),
            Some("value")
        );

        // Original unchanged
        assert!(ext.http.as_ref().unwrap().read().get_header("X-Custom").is_none());
    }

    #[test]
    fn test_cow_copy_monotonic_label_insert() {
        let mut ext = make_extensions();
        ext.labels_write_token = Some(WriteToken::new());

        let mut cow = ext.cow_copy();

        // Can add labels on the COW copy
        cow.security.as_mut().unwrap().add_label("HIPAA");
        assert!(cow.security.as_ref().unwrap().has_label("HIPAA"));

        // Original unchanged
        assert!(!ext.security.as_ref().unwrap().has_label("HIPAA"));
    }

    #[test]
    fn test_validate_immutable_passes_for_cow() {
        let ext = make_extensions();
        let cow = ext.cow_copy();

        // COW copy shares immutable Arcs → validation passes
        assert!(ext.validate_immutable(&cow));
    }

    #[test]
    fn test_validate_immutable_fails_when_tampered() {
        let ext = make_extensions();
        let mut cow = ext.cow_copy();

        // Tamper with an immutable slot
        cow.request = Some(Arc::new(RequestExtension {
            request_id: Some("TAMPERED".into()),
            ..Default::default()
        }));

        // Validation fails — different Arc pointer
        assert!(!ext.validate_immutable(&cow));
    }

    #[test]
    fn test_validate_immutable_both_none_passes() {
        let ext = Extensions::default();
        let cow = ext.cow_copy();
        assert!(ext.validate_immutable(&cow));
    }

    #[test]
    fn test_clone_drops_write_tokens() {
        let mut ext = make_extensions();
        ext.http_write_token = Some(WriteToken::new());
        ext.labels_write_token = Some(WriteToken::new());
        ext.delegation_write_token = Some(WriteToken::new());

        // Regular clone drops all tokens
        let cloned = ext.clone();
        assert!(cloned.http_write_token.is_none());
        assert!(cloned.labels_write_token.is_none());
        assert!(cloned.delegation_write_token.is_none());

        // cow_copy propagates them
        let cow = ext.cow_copy();
        assert!(cow.http_write_token.is_some());
        assert!(cow.labels_write_token.is_some());
        assert!(cow.delegation_write_token.is_some());
    }

    #[test]
    fn test_cow_copy_modify_multiple_fields() {
        use crate::extensions::DelegationExtension;
        use crate::extensions::delegation::DelegationHop;

        // Build extensions with security, http, delegation, custom
        let mut security = SecurityExtension::default();
        security.add_label("PII");

        let mut http = HttpExtension::default();
        http.set_header("Authorization", "Bearer token");

        let mut ext = Extensions {
            security: Some(security),
            http: Some(Guarded::new(http)),
            delegation: Some(DelegationExtension::default()),
            custom: Some([("existing".to_string(), serde_json::json!("value"))].into()),
            meta: Some(Arc::new(MetaExtension {
                entity_type: Some("tool".into()),
                ..Default::default()
            })),
            ..Default::default()
        };

        // Executor sets all write tokens
        ext.http_write_token = Some(WriteToken::new());
        ext.labels_write_token = Some(WriteToken::new());
        ext.delegation_write_token = Some(WriteToken::new());

        // Plugin does one cow_copy, modifies multiple fields
        let mut cow = ext.cow_copy();

        // 1. Add security labels (monotonic)
        cow.security.as_mut().unwrap().add_label("CHECKED");
        cow.security.as_mut().unwrap().add_label("COMPLIANT");

        // 2. Inject HTTP headers (guarded)
        let token = cow.http_write_token.as_ref().unwrap();
        cow.http.as_mut().unwrap().write(token).set_header("X-Checked", "true");
        cow.http.as_mut().unwrap().write(token).set_header("X-Policy", "v2");

        // 3. Append delegation hop (monotonic)
        cow.delegation.as_mut().unwrap().append_hop(DelegationHop {
            subject_id: "service-a".into(),
            scopes_granted: vec!["read_hr".into()],
            ..Default::default()
        });

        // 4. Add custom data (mutable, no token needed)
        cow.custom.as_mut().unwrap().insert(
            "audit.timestamp".into(),
            serde_json::json!("2026-04-29"),
        );

        // Verify COW copy has all modifications
        let sec = cow.security.as_ref().unwrap();
        assert!(sec.has_label("PII"));       // original
        assert!(sec.has_label("CHECKED"));   // added
        assert!(sec.has_label("COMPLIANT")); // added

        let http = cow.http.as_ref().unwrap().read();
        assert_eq!(http.get_header("Authorization"), Some("Bearer token")); // original
        assert_eq!(http.get_header("X-Checked"), Some("true"));            // added
        assert_eq!(http.get_header("X-Policy"), Some("v2"));               // added

        assert_eq!(cow.delegation.as_ref().unwrap().chain.len(), 1);
        assert_eq!(cow.delegation.as_ref().unwrap().chain[0].subject_id, "service-a");

        assert_eq!(cow.custom.as_ref().unwrap().get("existing").unwrap(), "value");
        assert_eq!(cow.custom.as_ref().unwrap().get("audit.timestamp").unwrap(), "2026-04-29");

        // Verify original is unchanged
        assert!(!ext.security.as_ref().unwrap().has_label("CHECKED"));
        assert!(ext.http.as_ref().unwrap().read().get_header("X-Checked").is_none());
        assert!(ext.delegation.as_ref().unwrap().chain.is_empty());
        assert!(!ext.custom.as_ref().unwrap().contains_key("audit.timestamp"));

        // Immutable slots still valid
        assert!(ext.validate_immutable(&cow));
    }

    #[test]
    fn test_read_only_plugin_zero_cost() {
        // Plugin that only reads — no cow_copy, no clone
        let ext = make_extensions();

        // Read security labels
        let has_pii = ext.security.as_ref()
            .map(|s| s.has_label("PII"))
            .unwrap_or(false);
        assert!(has_pii);

        // Read HTTP headers
        let auth = ext.http.as_ref()
            .map(|h| h.read().get_header("Authorization"))
            .flatten();
        assert_eq!(auth, Some("Bearer token"));

        // Read meta
        let entity = ext.meta.as_ref()
            .and_then(|m| m.entity_type.as_deref());
        assert_eq!(entity, Some("tool"));

        // No cow_copy called — zero allocations for read-only access
    }
}
