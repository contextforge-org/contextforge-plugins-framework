// Location: ./crates/cpex-core/src/decision.rs
// Copyright 2026
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// The DecisionLog — the executor's private, append-only record of what
// each plugin did to a request and how the pipeline ruled on it.
//
// Why it exists: a plugin observing a request (audit-logger, ocsf-audit)
// cannot see the pipeline's verdict — allow/deny/modify lives in the
// executor's control flow (`PluginResult`, the short-circuit return), not
// in `Extensions`. The DecisionLog captures that control flow so an audit
// sink can serialize it. It is built by the executor and handed only to
// audit handlers; it is deliberately NOT placed on `PluginContext`, which
// every plugin can read — the component that records must not be readable
// (or writable) by the components it records.
//
// Kept cheap: it records what happened (which plugin, which phase, which
// action), not copies of payloads.

use crate::error::PluginViolation;
use crate::plugin::PluginMode;

/// What a single plugin did to the request, from the executor's point of
/// view. Derived from the plugin's `PluginResult`, not self-reported.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PluginAction {
    /// Ran and let the request continue unchanged.
    Allowed,
    /// Blocked the request. The full violation rides on the terminal
    /// [`Verdict::Deny`]; this marks *which* plugin, in order.
    Denied,
    /// Replaced the payload (accepted by the executor's modify path).
    ModifiedPayload,
    /// Wrote to an extension slot it was capable of writing.
    ModifiedExtensions,
    /// Failed. The string is the error rendered by the executor; whether
    /// this halts the pipeline is decided by the plugin's `on_error`.
    Error(String),
}

/// One entry in the log: a plugin, the phase it ran in, and what it did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecisionStep {
    /// The plugin instance name (`PluginConfig.name`).
    pub plugin_name: String,
    /// The phase this plugin ran in — Sequential / Transform / Audit / …
    pub phase: PluginMode,
    /// What it did.
    pub action: PluginAction,
}

/// The pipeline's terminal ruling on a request.
#[derive(Debug, Clone)]
pub enum Verdict {
    /// The request was allowed through (possibly after modifications —
    /// those are in [`DecisionLog::steps`]).
    Allow,
    /// The request was blocked. Carries the fully-formed violation the
    /// executor stamped with the deciding plugin's name.
    Deny(PluginViolation),
}

impl Verdict {
    /// True if this verdict blocked the request.
    pub fn is_deny(&self) -> bool {
        matches!(self, Verdict::Deny(_))
    }
}

/// The W3C trace context for one pipeline invocation — the node identity in
/// the decision graph. `span_id` is this interception's own span,
/// `parent_span_id` is the upstream call that triggered it (the causal edge),
/// and `trace_id` correlates the whole run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Span {
    /// The trace this invocation belongs to (W3C trace-id: 32 hex chars).
    pub trace_id: String,
    /// This interception's own span (W3C span-id: 16 hex chars).
    pub span_id: String,
    /// The span of the upstream call that caused this one — the causal edge.
    /// `None` when the request carried no trace context (a trace root).
    pub parent_span_id: Option<String>,
}

impl Span {
    /// Derive the span for an interception from the request's trace context,
    /// child-span model: a fresh `span_id` for this interception, the request's
    /// `span_id` as the causal parent, and the request's `trace_id` carried
    /// through — or a freshly originated trace root when the request carries
    /// none. Adopts W3C ids; it does not invent a bespoke scheme.
    pub fn for_request(trace_id: Option<&str>, parent_span_id: Option<&str>) -> Self {
        Self {
            trace_id: trace_id.map(str::to_owned).unwrap_or_else(new_trace_id),
            span_id: new_span_id(),
            parent_span_id: parent_span_id.map(str::to_owned),
        }
    }
}

/// A freshly originated W3C trace-id: 16 bytes / 32 lowercase hex chars.
fn new_trace_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

/// A freshly originated W3C span-id: 8 bytes / 16 lowercase hex chars (the
/// first half of a UUID's hex).
fn new_span_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..16].to_string()
}

/// The executor's record of one pipeline invocation: the ordered steps
/// each plugin took, the terminal verdict, and this invocation's span.
///
/// `verdict` is `None` while the pipeline is still running and is set once
/// at a return point (allow or deny). An audit sink always receives a
/// finalized log.
#[derive(Debug, Clone, Default)]
pub struct DecisionLog {
    steps: Vec<DecisionStep>,
    verdict: Option<Verdict>,
    span: Option<Span>,
    input_labels: Vec<String>,
    input_hash: Option<String>,
}

impl DecisionLog {
    /// A fresh log for one pipeline invocation.
    pub fn new() -> Self {
        Self::default()
    }

    /// Append what a plugin did. Called by the executor as each plugin
    /// returns; order is execution order.
    pub fn record(
        &mut self,
        plugin_name: impl Into<String>,
        phase: PluginMode,
        action: PluginAction,
    ) {
        self.steps.push(DecisionStep {
            plugin_name: plugin_name.into(),
            phase,
            action,
        });
    }

    /// Set the terminal verdict. Called once at the pipeline's return
    /// point, before the log is handed to audit handlers.
    pub fn finalize(&mut self, verdict: Verdict) {
        self.verdict = Some(verdict);
    }

    /// Attach this invocation's span (trace context). Called by the executor
    /// at pipeline entry, derived from the request via [`Span::for_request`].
    pub fn set_span(&mut self, span: Span) {
        self.span = Some(span);
    }

    /// This invocation's span (trace context) — the node identity and causal
    /// parent for the decision graph — if the executor set one.
    pub fn span(&self) -> Option<&Span> {
        self.span.as_ref()
    }

    /// Record the taint labels the request carried at pipeline entry — the
    /// input side of this node's provenance. Diffed against the final labels
    /// (on `Extensions.security`), it yields the taint the pipeline added.
    pub fn set_input_labels(&mut self, labels: Vec<String>) {
        self.input_labels = labels;
    }

    /// The taint labels present at pipeline entry.
    pub fn input_labels(&self) -> &[String] {
        &self.input_labels
    }

    /// Record the content hash of the payload at pipeline entry — the input
    /// side of this node's content provenance. Set by the executor only when
    /// content provenance is enabled; otherwise `None`.
    pub fn set_input_hash(&mut self, hash: Option<String>) {
        self.input_hash = hash;
    }

    /// The content hash of the payload at pipeline entry, if captured.
    pub fn input_hash(&self) -> Option<&str> {
        self.input_hash.as_deref()
    }

    /// The ordered steps taken this invocation.
    pub fn steps(&self) -> &[DecisionStep] {
        &self.steps
    }

    /// The terminal verdict, or `None` if the pipeline hasn't returned yet.
    pub fn verdict(&self) -> Option<&Verdict> {
        self.verdict.as_ref()
    }

    /// True once finalized with a deny.
    pub fn is_denied(&self) -> bool {
        self.verdict.as_ref().is_some_and(Verdict::is_deny)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn violation() -> PluginViolation {
        PluginViolation::new("missing_permission", "not allowed")
    }

    #[test]
    fn records_steps_in_order() {
        let mut log = DecisionLog::new();
        log.record(
            "pii-scanner",
            PluginMode::Transform,
            PluginAction::ModifiedPayload,
        );
        log.record("cedar-pdp", PluginMode::Sequential, PluginAction::Denied);

        let steps = log.steps();
        assert_eq!(steps.len(), 2);
        assert_eq!(steps[0].plugin_name, "pii-scanner");
        assert_eq!(steps[0].action, PluginAction::ModifiedPayload);
        assert_eq!(steps[1].phase, PluginMode::Sequential);
        assert_eq!(steps[1].action, PluginAction::Denied);
    }

    #[test]
    fn verdict_is_none_until_finalized() {
        let mut log = DecisionLog::new();
        assert!(log.verdict().is_none());
        assert!(!log.is_denied());

        log.finalize(Verdict::Deny(violation()));
        assert!(log.is_denied());
        match log.verdict() {
            Some(Verdict::Deny(v)) => assert_eq!(v.code, "missing_permission"),
            other => panic!("expected deny, got {other:?}"),
        }
    }

    #[test]
    fn allow_verdict_is_not_a_deny() {
        let mut log = DecisionLog::new();
        log.finalize(Verdict::Allow);
        assert!(!log.is_denied());
    }

    #[test]
    fn span_child_model_carries_causal_edge() {
        let span = Span::for_request(Some("trace-abc"), Some("upstream-span"));
        assert_eq!(span.trace_id, "trace-abc", "trace carried through");
        assert_eq!(
            span.parent_span_id.as_deref(),
            Some("upstream-span"),
            "the request's span becomes the causal parent"
        );
        assert_eq!(span.span_id.len(), 16, "own fresh W3C span-id");
        assert_ne!(span.span_id, "upstream-span", "our span, not the parent's");
    }

    #[test]
    fn span_originates_trace_root_when_request_has_none() {
        let span = Span::for_request(None, None);
        assert_eq!(span.trace_id.len(), 32, "originated W3C trace-id");
        assert_eq!(span.span_id.len(), 16, "originated W3C span-id");
        assert!(span.parent_span_id.is_none(), "no parent = trace root");
    }

    #[test]
    fn each_invocation_gets_a_distinct_span() {
        let a = Span::for_request(Some("t"), Some("p"));
        let b = Span::for_request(Some("t"), Some("p"));
        assert_ne!(a.span_id, b.span_id, "each interception mints its own span");
    }

    #[test]
    fn span_is_none_until_set() {
        let mut log = DecisionLog::new();
        assert!(log.span().is_none());
        log.set_span(Span::for_request(Some("t"), None));
        assert_eq!(log.span().unwrap().trace_id, "t");
    }

    #[test]
    fn input_labels_default_empty_and_settable() {
        let mut log = DecisionLog::new();
        assert!(log.input_labels().is_empty());
        log.set_input_labels(vec!["PII".into(), "secret".into()]);
        assert_eq!(
            log.input_labels(),
            &["PII".to_string(), "secret".to_string()]
        );
    }

    #[test]
    fn input_hash_default_none_and_settable() {
        let mut log = DecisionLog::new();
        assert!(log.input_hash().is_none());
        log.set_input_hash(Some("sha256:abc".into()));
        assert_eq!(log.input_hash(), Some("sha256:abc"));
    }
}
