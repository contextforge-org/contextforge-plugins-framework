// Location: ./crates/cpex-core/src/audit.rs
// Copyright 2026
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// The audit-hook consumer: an observation-only sink invoked at the
// pipeline's verdict with the decision log.
//
// This is deliberately NOT a `HookHandler<H>` (whose `handle` returns a
// `PluginResult` and so can allow/deny/modify). An audit sink returns
// nothing — the type is the contract: it *sees* the verdict and every
// plugin's action, but it cannot influence them. The manager auto-attaches
// these; the executor invokes them once per pipeline run, at the verdict,
// with the final payload, extensions, and the decision log. The decision
// log is passed directly here and never placed on `PluginContext`, so no
// ordinary plugin can read what the audit sink reads.

use async_trait::async_trait;

use crate::decision::DecisionLog;
use crate::effect::EffectRecord;
use crate::hooks::payload::{Extensions, PluginPayload};

/// An observation-only consumer of pipeline decisions.
///
/// Implemented by audit plugins (e.g. `audit-logger`, `ocsf-audit`). The
/// executor calls [`AuditHandler::handle`] once per pipeline invocation,
/// after the verdict is decided, for both allowed and denied requests.
#[async_trait]
pub trait AuditHandler: Send + Sync {
    /// Observe one finished pipeline invocation. Must not block or mutate
    /// anything the pipeline depends on — its return is `()` by design.
    ///
    /// * `payload` — the message as it stood at the verdict.
    /// * `extensions` — the final extensions (identity, delegation, labels…).
    /// * `decisions` — what each plugin did and how the pipeline ruled.
    async fn handle(
        &self,
        payload: &dyn PluginPayload,
        extensions: &Extensions,
        decisions: &DecisionLog,
    );

    /// Observe an irreversible external effect a plugin *caused* — a token
    /// mint, an approval grant — as its own event, separate from the
    /// per-invocation decision. Fired at each lifecycle transition
    /// (`prepared` → `confirmed` | `rejected` | `unknown`).
    ///
    /// `extensions` carries the same ambient context a decision sink gets —
    /// identity, delegation, correlation (conversation / span) — so a sink can
    /// build a correlatable, richly-typed event (e.g. an OCSF Authentication
    /// event for a token mint, in the same attestation chain) rather than
    /// working from the effect alone. `EffectRecord` stays effect-specific.
    ///
    /// Default: ignore. A sink that only cares about decisions need not
    /// implement this; a sink that cares about effects overrides it.
    async fn on_effect(&self, _effect: &EffectRecord, _extensions: &Extensions) {}

    /// A short identifier used in error logs when a sink panics or times
    /// out. Defaults to `"audit"`; override to distinguish sinks.
    fn name(&self) -> &str {
        "audit"
    }
}
