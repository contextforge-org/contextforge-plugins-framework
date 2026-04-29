// Location: ./crates/cpex-core/src/extensions/http.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// HttpExtension — HTTP headers.
// Mirrors cpex/framework/extensions/http.py.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// HTTP-related extensions.
///
/// Carries HTTP headers. Capability-gated: requires `read_headers`
/// to see, `write_headers` to modify.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct HttpExtension {
    /// HTTP headers as key-value pairs.
    #[serde(default)]
    pub headers: HashMap<String, String>,
}

impl HttpExtension {
    /// Set a header (overwrites if exists).
    pub fn set_header(&mut self, name: impl Into<String>, value: impl Into<String>) {
        self.headers.insert(name.into(), value.into());
    }

    /// Get a header value (case-insensitive lookup).
    pub fn get_header(&self, name: &str) -> Option<&str> {
        let lower = name.to_lowercase();
        self.headers
            .iter()
            .find(|(k, _)| k.to_lowercase() == lower)
            .map(|(_, v)| v.as_str())
    }

    /// Check if a header exists (case-insensitive).
    pub fn has_header(&self, name: &str) -> bool {
        self.get_header(name).is_some()
    }

    /// Add header only if it doesn't exist. Returns true if added.
    pub fn add_header(&mut self, name: impl Into<String>, value: impl Into<String>) -> bool {
        let name = name.into();
        if self.has_header(&name) {
            return false;
        }
        self.headers.insert(name, value.into());
        true
    }

    /// Remove a header by name. Returns the removed value.
    pub fn remove_header(&mut self, name: &str) -> Option<String> {
        let lower = name.to_lowercase();
        let key = self
            .headers
            .keys()
            .find(|k| k.to_lowercase() == lower)
            .cloned();
        key.and_then(|k| self.headers.remove(&k))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_set_and_get_header() {
        let mut http = HttpExtension::default();
        http.set_header("Content-Type", "application/json");
        assert_eq!(http.get_header("Content-Type"), Some("application/json"));
    }

    #[test]
    fn test_get_header_case_insensitive() {
        let mut http = HttpExtension::default();
        http.set_header("Authorization", "Bearer tok");
        assert_eq!(http.get_header("authorization"), Some("Bearer tok"));
        assert_eq!(http.get_header("AUTHORIZATION"), Some("Bearer tok"));
    }

    #[test]
    fn test_has_header() {
        let mut http = HttpExtension::default();
        assert!(!http.has_header("X-Custom"));
        http.set_header("X-Custom", "value");
        assert!(http.has_header("X-Custom"));
        assert!(http.has_header("x-custom")); // case-insensitive
    }

    #[test]
    fn test_add_header_only_if_absent() {
        let mut http = HttpExtension::default();
        assert!(http.add_header("X-New", "first"));
        assert!(!http.add_header("X-New", "second")); // already exists
        assert_eq!(http.get_header("X-New"), Some("first"));
    }

    #[test]
    fn test_set_header_overwrites() {
        let mut http = HttpExtension::default();
        http.set_header("X-Val", "old");
        http.set_header("X-Val", "new");
        assert_eq!(http.get_header("X-Val"), Some("new"));
    }

    #[test]
    fn test_remove_header() {
        let mut http = HttpExtension::default();
        http.set_header("X-Remove", "value");
        let removed = http.remove_header("x-remove"); // case-insensitive
        assert_eq!(removed, Some("value".to_string()));
        assert!(!http.has_header("X-Remove"));
    }

    #[test]
    fn test_remove_nonexistent_header() {
        let mut http = HttpExtension::default();
        assert!(http.remove_header("X-Missing").is_none());
    }

    #[test]
    fn test_serde_roundtrip() {
        let mut http = HttpExtension::default();
        http.set_header("Authorization", "Bearer tok");
        http.set_header("X-Request-ID", "req-123");

        let json = serde_json::to_string(&http).unwrap();
        let deserialized: HttpExtension = serde_json::from_str(&json).unwrap();

        assert_eq!(deserialized.get_header("Authorization"), Some("Bearer tok"));
        assert_eq!(deserialized.get_header("X-Request-ID"), Some("req-123"));
    }
}
