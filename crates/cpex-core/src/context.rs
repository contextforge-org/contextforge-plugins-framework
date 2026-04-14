// Location: ./crates/cpex-core/src/context.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// Execution context types.
//
// Provides GlobalContext (shared across all plugins for a request) and
// PluginContext (per-plugin, per-invocation). These carry transient
// execution state — counters, caches, intermediate results. All data
// needed for policy evaluation comes from the payload's extensions
// (filtered by capabilities), not from context.
//
// Mirrors the Python framework's GlobalContext and PluginContext types
// in cpex/framework/models.py.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

// ---------------------------------------------------------------------------
// Global Context
// ---------------------------------------------------------------------------

/// Shared execution context for a single request.
///
/// Visible to all plugins during a hook invocation. Plugins can read
/// and contribute to `state` (mutable shared state) and read `metadata`
/// (read-only shared metadata set by the host).
///
/// # Fields
///
/// * `request_id` — Unique identifier for the current request.
/// * `user` — User identifier or principal (string or structured).
/// * `tenant_id` — Optional multi-tenant scope.
/// * `server_id` — Optional virtual server scope.
/// * `state` — Mutable shared state merged between plugins.
/// * `metadata` — Read-only metadata set by the host before dispatch.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GlobalContext {
    /// Unique request identifier (typically a UUID).
    pub request_id: String,

    /// User identifier or principal.
    #[serde(default)]
    pub user: Value,

    /// Multi-tenant scope identifier.
    #[serde(default)]
    pub tenant_id: Option<String>,

    /// Virtual server scope identifier.
    #[serde(default)]
    pub server_id: Option<String>,

    /// Mutable shared state — plugins can read and contribute.
    /// Merged back after each plugin execution.
    #[serde(default)]
    pub state: HashMap<String, Value>,

    /// Read-only shared metadata set by the host.
    #[serde(default)]
    pub metadata: HashMap<String, Value>,
}

impl GlobalContext {
    /// Create a new context with just a request ID.
    pub fn new(request_id: impl Into<String>) -> Self {
        Self {
            request_id: request_id.into(),
            user: Value::Null,
            tenant_id: None,
            server_id: None,
            state: HashMap::new(),
            metadata: HashMap::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// Plugin Context
// ---------------------------------------------------------------------------

/// Per-plugin, per-invocation execution context.
///
/// Each plugin receives its own `PluginContext` with isolated local
/// state and a reference to the shared global context. The framework
/// deep-copies the global context per plugin to prevent cross-plugin
/// state leakage.
///
/// This type carries transient execution state only — counters, caches,
/// intermediate results. All data needed for policy evaluation comes
/// from the payload's extensions (filtered by capabilities).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginContext {
    /// Plugin-local state. Private to this plugin, this invocation.
    #[serde(default)]
    pub local_state: HashMap<String, Value>,

    /// Snapshot of the global context for this invocation.
    pub global_context: GlobalContext,
}

impl PluginContext {
    /// Create a new plugin context from a global context.
    pub fn new(global_context: GlobalContext) -> Self {
        Self {
            local_state: HashMap::new(),
            global_context,
        }
    }

    /// Get a value from local state.
    pub fn get_local(&self, key: &str) -> Option<&Value> {
        self.local_state.get(key)
    }

    /// Set a value in local state.
    pub fn set_local(&mut self, key: impl Into<String>, value: Value) {
        self.local_state.insert(key.into(), value);
    }
}
