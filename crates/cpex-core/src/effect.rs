// Location: ./crates/cpex-core/src/effect.rs
// Copyright 2026
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// EffectRecord — an irreversible external effect a plugin *causes* (a token
// mint, an approval grant), audited as its own event, distinct from a
// pipeline decision.
//
// Effects follow a small transaction lifecycle:
//
//     prepared -> confirmed | rejected | unknown
//
// `prepared` means CPEX has *durably recorded the intent* to attempt the
// action and nothing external has happened yet; the terminal states record
// the outcome, or `unknown` after a crash (resolved later by reconciling
// against the participant — e.g. the IdP — via the record's `key`). This is
// the write-ahead model in docs/step5-effect-audit-options.md (Option C).
//
// This slice is the type + lifecycle only. The emit path
// (`begin_effect`/`complete_effect`), the durable sink, and framework-
// mediated effect primitives are later slices.

use std::collections::HashMap;

use async_trait::async_trait;

use crate::error::PluginError;
use crate::hooks::payload::Extensions;

/// Where an effect is in its lifecycle.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum EffectState {
    /// Intent durably recorded; nothing external has happened yet.
    Prepared,
    /// The external act completed.
    Confirmed,
    /// The external act provably did not happen.
    Rejected,
    /// Crashed after acting, before the outcome was recorded. Resolved by
    /// reconciling against the participant via `EffectRecord::key`.
    Unknown,
}

/// A record of an irreversible external effect — emitted to audit sinks as
/// its own event. The causing plugin fills the descriptive fields; the
/// framework stamps `plugin_name`.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct EffectRecord {
    /// Machine-readable kind, e.g. `"token_mint"`, `"approval_grant"`.
    pub kind: String,
    /// Human-readable description.
    pub description: String,
    /// Idempotency / reconciliation key threaded into the external call, so
    /// an `unknown` outcome can be resolved against the participant later.
    pub key: String,
    /// Where in its lifecycle this record is.
    pub state: EffectState,
    /// Structured, effect-specific detail (audience, scopes, ttl, …).
    pub details: HashMap<String, serde_json::Value>,
    /// Which plugin caused the effect. Set by the framework, not self-reported.
    pub plugin_name: Option<String>,
}

impl EffectRecord {
    /// A fresh `prepared` record — the intent, before the act. `key` is the
    /// idempotency/reconciliation key that will ride into the external call.
    pub fn prepared(
        kind: impl Into<String>,
        description: impl Into<String>,
        key: impl Into<String>,
    ) -> Self {
        Self {
            kind: kind.into(),
            description: description.into(),
            key: key.into(),
            state: EffectState::Prepared,
            details: HashMap::new(),
            plugin_name: None,
        }
    }

    /// Attach a structured detail (builder-style).
    pub fn with_detail(mut self, key: impl Into<String>, value: impl Into<serde_json::Value>) -> Self {
        self.details.insert(key.into(), value.into());
        self
    }

    /// Move to a terminal state (`Confirmed` / `Rejected` / `Unknown`).
    pub fn into_state(mut self, state: EffectState) -> Self {
        self.state = state;
        self
    }
}

/// A per-invocation capability handle for emitting effects, granted to
/// plugins with the `emit_effect` capability. It rides on `Extensions`
/// exactly like the write tokens: `filter_extensions` never sets it; the
/// executor does, for capable plugins, right before `handle`. Calling it
/// fans the record out to the auto-attached audit sinks' `on_effect`.
///
/// Slice 2 emits (write-ahead *ordering*); it is not yet *durable* — the WAL
/// and fail-closed prepare are a later slice.
#[async_trait]
pub trait EffectEmitter: Send + Sync + std::fmt::Debug {
    /// Durably record `effect`, then fan it out to the audit sinks. `ext`
    /// supplies ambient context (identity, delegation, correlation).
    ///
    /// **Fail-closed:** an `Err` means the record could not be durably
    /// persisted, so the caller must **not** perform the irreversible act.
    /// (With no durable log configured, this is ordering-only and returns
    /// `Ok` after emitting — slice 3b installs the real WAL.)
    async fn emit(&self, effect: &EffectRecord, ext: &Extensions) -> Result<(), Box<PluginError>>;
}

/// A durable, append-only sink for effect records — the write-ahead log.
/// `append` must not return `Ok` until the record is durably persisted; an
/// `Err` means the caller must **not** perform the irreversible act
/// (fail-closed). Slice 3a defines the contract; a file-backed impl and the
/// crash-recovery sweep are slice 3b.
#[async_trait]
pub trait DurableEffectLog: Send + Sync {
    async fn append(&self, effect: &EffectRecord) -> Result<(), Box<PluginError>>;
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn prepared_starts_in_prepared_with_details() {
        let e = EffectRecord::prepared("token_mint", "exchange for workday-api", "k-1")
            .with_detail("audience", "workday-api")
            .with_detail("scopes", json!(["read_compensation"]));

        assert_eq!(e.kind, "token_mint");
        assert_eq!(e.key, "k-1");
        assert_eq!(e.state, EffectState::Prepared);
        assert_eq!(e.details["audience"], "workday-api");
        assert_eq!(e.details["scopes"][0], "read_compensation");
        assert!(e.plugin_name.is_none());
    }

    #[test]
    fn into_state_transitions_to_terminal() {
        let e = EffectRecord::prepared("token_mint", "…", "k-2").into_state(EffectState::Confirmed);
        assert_eq!(e.state, EffectState::Confirmed);
    }
}
