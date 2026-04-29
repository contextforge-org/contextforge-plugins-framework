// Location: ./crates/cpex-core/src/extensions/meta.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// MetaExtension — host-provided operational metadata.
// Mirrors cpex/framework/extensions/meta.py.

use std::collections::{HashMap, HashSet};

use serde::{Deserialize, Serialize};

/// Host-provided operational metadata.
///
/// Carries entity identification for route resolution, tags for
/// policy group inheritance, scope for host-defined grouping,
/// and arbitrary properties.
///
/// Immutable — set by the host before invoking the hook.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct MetaExtension {
    /// Operational tags — drive policy group inheritance.
    #[serde(default)]
    pub tags: HashSet<String>,

    /// Host-defined grouping (virtual server ID, namespace, etc.).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<String>,

    /// Arbitrary key-value metadata.
    #[serde(default)]
    pub properties: HashMap<String, String>,
}
