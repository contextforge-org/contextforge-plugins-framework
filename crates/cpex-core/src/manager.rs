// Location: ./crates/cpex-core/src/manager.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// Plugin manager.
//
// Owns the plugin lifecycle (initialize, dispatch, shutdown) and
// the PluginRegistry. Provides two invoke paths:
//
// - `invoke::<H>()` — typed dispatch for Rust callers. Zero-cost.
//   The hook type is known at compile time; no registry lookup or
//   downcast needed for the payload.
//
// - `invoke_by_name()` — dynamic dispatch for Python/Go/WASM callers.
//   Hook name resolved from the registry; payload passed as
//   Box<dyn PluginPayload>.
//
// The manager reads plugin configs from the config loader and wraps
// each plugin in a PluginRef with the authoritative config. Plugins
// never provide their own config to the manager. Trust flows:
//   config loader → manager → PluginRef → executor
//
// Mirrors the Python framework's PluginManager in
// cpex/framework/manager.py.

use std::sync::Arc;

use tracing::{error, info};

use crate::context::GlobalContext;
use crate::error::PluginError;
use crate::executor::{Executor, ExecutorConfig, PipelineResult};
use crate::hooks::adapter::TypedHandlerAdapter;
use crate::hooks::payload::{Extensions, PluginPayload};
use crate::hooks::trait_def::{HookHandler, HookTypeDef, PluginResult};
use crate::hooks::HookType;
use crate::plugin::{Plugin, PluginConfig};
use crate::registry::{AnyHookHandler, PluginRef, PluginRegistry};

// ---------------------------------------------------------------------------
// Manager Configuration
// ---------------------------------------------------------------------------

/// Configuration for the PluginManager.
#[derive(Debug, Clone)]
pub struct ManagerConfig {
    /// Executor configuration (timeout, short-circuit behavior).
    pub executor: ExecutorConfig,
}

impl Default for ManagerConfig {
    fn default() -> Self {
        Self {
            executor: ExecutorConfig::default(),
        }
    }
}

// ---------------------------------------------------------------------------
// Plugin Manager
// ---------------------------------------------------------------------------

/// Central plugin lifecycle and dispatch manager.
///
/// Owns the plugin registry and executor. Provides the public API
/// that host systems (ContextForge, Kagenti, etc.) call to register
/// plugins and invoke hooks.
///
/// # Lifecycle
///
/// ```text
/// new() → register plugins → initialize() → invoke hooks → shutdown()
/// ```
///
/// # Two Invoke Paths
///
/// - **`invoke::<H>()`** — typed dispatch. The hook type `H` is known
///   at compile time. Payload type-checked at compile time. Used by
///   Rust callers.
///
/// - **`invoke_by_name()`** — dynamic dispatch. The hook name is a
///   string. Payload is `Box<dyn PluginPayload>`. Used by Python/Go/WASM
///   callers via the FFI or PyO3 bindings.
///
/// Both paths use the same registry, executor, and 5-phase pipeline.
///
/// # Trust Model
///
/// The manager wraps each plugin in a `PluginRef` with an authoritative
/// config from the config loader. The executor reads all scheduling
/// decisions from `PluginRef.trusted_config` — never from the plugin.
pub struct PluginManager {
    /// Plugin registry — stores PluginRefs and hook-to-handler mappings.
    registry: PluginRegistry,

    /// Executor — stateless 5-phase pipeline engine.
    executor: Executor,

    /// Manager configuration.
    config: ManagerConfig,

    /// Whether initialize() has been called.
    initialized: bool,
}

impl PluginManager {
    /// Create a new PluginManager with the given configuration.
    pub fn new(config: ManagerConfig) -> Self {
        Self {
            registry: PluginRegistry::new(),
            executor: Executor::new(config.executor.clone()),
            config,
            initialized: false,
        }
    }

    // -----------------------------------------------------------------------
    // Registration
    // -----------------------------------------------------------------------

    /// Register a plugin handler for its primary hook name.
    ///
    /// This is the preferred registration method. The framework creates
    /// the type-erased adapter internally — no `AnyHookHandler` needed.
    ///
    /// # Type Parameters
    ///
    /// - `H` — the hook type (implements `HookTypeDef`).
    /// - `P` — the plugin type (implements `Plugin + HookHandler<H>`).
    ///
    /// # Arguments
    ///
    /// - `plugin` — the plugin implementation.
    /// - `config` — authoritative config from the config loader.
    ///
    /// # Examples
    ///
    /// ```rust,ignore
    /// manager.register_handler::<CmfHook, _>(plugin, config)?;
    /// ```
    pub fn register_handler<H, P>(
        &mut self,
        plugin: Arc<P>,
        config: PluginConfig,
    ) -> Result<(), PluginError>
    where
        H: HookTypeDef,
        H::Result: Into<PluginResult<H::Payload>>,
        P: Plugin + HookHandler<H> + 'static,
    {
        let handler: Arc<dyn AnyHookHandler> =
            Arc::new(TypedHandlerAdapter::<H, P>::new(Arc::clone(&plugin)));
        self.registry
            .register::<H>(plugin, config, handler)
            .map_err(|msg| PluginError::Config { message: msg })
    }

    /// Register a plugin handler for multiple hook names.
    ///
    /// This is the CMF pattern — one handler covers multiple hook
    /// names (`cmf.tool_pre_invoke`, `cmf.llm_input`, etc.).
    ///
    /// # Examples
    ///
    /// ```rust,ignore
    /// manager.register_handler_for_names::<CmfHook, _>(
    ///     plugin, config,
    ///     &["cmf.tool_pre_invoke", "cmf.llm_input", "cmf.llm_output"],
    /// )?;
    /// ```
    pub fn register_handler_for_names<H, P>(
        &mut self,
        plugin: Arc<P>,
        config: PluginConfig,
        names: &[&str],
    ) -> Result<(), PluginError>
    where
        H: HookTypeDef,
        H::Result: Into<PluginResult<H::Payload>>,
        P: Plugin + HookHandler<H> + 'static,
    {
        let handler: Arc<dyn AnyHookHandler> =
            Arc::new(TypedHandlerAdapter::<H, P>::new(Arc::clone(&plugin)));
        self.registry
            .register_for_names::<H>(plugin, config, handler, names)
            .map_err(|msg| PluginError::Config { message: msg })
    }

    /// Register with an explicit AnyHookHandler (advanced use).
    ///
    /// For cases where the automatic adapter doesn't fit — e.g.,
    /// Python/WASM bridge hosts that implement AnyHookHandler directly.
    /// Most callers should use `register_handler` instead.
    pub fn register_raw<H: HookTypeDef>(
        &mut self,
        plugin: Arc<dyn Plugin>,
        config: PluginConfig,
        handler: Arc<dyn AnyHookHandler>,
    ) -> Result<(), PluginError> {
        self.registry
            .register::<H>(plugin, config, handler)
            .map_err(|msg| PluginError::Config { message: msg })
    }

    /// Register a plugin using hook names from its config (legacy path).
    ///
    /// No typed handler — the plugin is registered in the name index
    /// only. Used for backward compatibility with plugins that don't
    /// use the typed hook system.
    pub fn register_legacy(
        &mut self,
        plugin: Arc<dyn Plugin>,
        config: PluginConfig,
    ) -> Result<(), PluginError> {
        self.registry
            .register_legacy(plugin, config)
            .map_err(|msg| PluginError::Config { message: msg })
    }

    // -----------------------------------------------------------------------
    // Lifecycle
    // -----------------------------------------------------------------------

    /// Initialize all registered plugins.
    ///
    /// Calls `plugin.initialize()` on each registered plugin. Must be
    /// called before invoking any hooks. Idempotent — calling twice
    /// has no effect.
    pub async fn initialize(&mut self) -> Result<(), PluginError> {
        if self.initialized {
            return Ok(());
        }

        info!(
            "Initializing PluginManager with {} plugins",
            self.registry.plugin_count()
        );

        for name in self.registry.plugin_names() {
            if let Some(plugin_ref) = self.registry.get(name) {
                let plugin = plugin_ref.plugin().clone();
                let plugin_name = name.to_string();

                if let Err(e) = plugin.initialize().await {
                    error!("Failed to initialize plugin '{}': {}", plugin_name, e);
                    return Err(PluginError::Execution {
                        plugin_name,
                        message: format!("initialization failed: {}", e),
                        source: Some(Box::new(e)),
                    });
                }
            }
        }

        self.initialized = true;
        info!("PluginManager initialized successfully");
        Ok(())
    }

    /// Shutdown all registered plugins.
    ///
    /// Calls `plugin.shutdown()` on each registered plugin in reverse
    /// registration order. Errors are logged but do not halt the
    /// shutdown process — all plugins get a chance to clean up.
    pub async fn shutdown(&mut self) {
        if !self.initialized {
            return;
        }

        info!("Shutting down PluginManager");

        for name in self.registry.plugin_names() {
            if let Some(plugin_ref) = self.registry.get(name) {
                let plugin = plugin_ref.plugin().clone();

                if let Err(e) = plugin.shutdown().await {
                    error!("Error shutting down plugin '{}': {}", name, e);
                    // Continue — don't let one plugin's failure block others
                }
            }
        }

        self.initialized = false;
        info!("PluginManager shutdown complete");
    }

    // -----------------------------------------------------------------------
    // Hook Invocation — Dynamic (invoke_by_name)
    // -----------------------------------------------------------------------

    /// Invoke a hook by name with a type-erased payload.
    ///
    /// This is the dynamic dispatch path used by Python/Go/WASM
    /// callers via FFI or PyO3 bindings. The hook name is resolved
    /// from the registry and dispatched through the 5-phase executor.
    ///
    /// # Arguments
    ///
    /// * `hook_name` — the hook name string (e.g., `"cmf.tool_pre_invoke"`).
    /// * `payload` — the payload as `Box<dyn PluginPayload>`.
    /// * `extensions` — the full extensions (filtered per plugin by the executor).
    /// * `global_ctx` — shared request context.
    ///
    /// # Returns
    ///
    /// A `PipelineResult` with the final payload, extensions, and
    /// any violation.
    pub async fn invoke_by_name(
        &self,
        hook_name: &str,
        payload: Box<dyn PluginPayload>,
        extensions: Extensions,
        global_ctx: &GlobalContext,
    ) -> PipelineResult {
        let hook_type = HookType::new(hook_name);
        let entries = self.registry.entries_for_hook(&hook_type);

        if entries.is_empty() {
            return PipelineResult::allowed_with(payload, extensions);
        }

        self.executor
            .execute(entries, payload, extensions, global_ctx)
            .await
    }

    // -----------------------------------------------------------------------
    // Hook Invocation — Typed (invoke::<H>)
    // -----------------------------------------------------------------------

    /// Invoke a typed hook.
    ///
    /// This is the compile-time dispatch path used by Rust callers.
    /// The hook type `H` determines the payload and result types.
    /// Dispatch goes through the same registry and 5-phase executor
    /// as `invoke_by_name()`.
    ///
    /// # Type Parameters
    ///
    /// - `H` — the hook type (implements `HookTypeDef`).
    ///
    /// # Arguments
    ///
    /// * `payload` — the typed payload.
    /// * `extensions` — the full extensions.
    /// * `global_ctx` — shared request context.
    ///
    /// # Returns
    ///
    /// A `PipelineResult` with the final payload (type-erased —
    /// caller downcasts via `as_any()`), extensions, and any violation.
    pub async fn invoke<H: HookTypeDef>(
        &self,
        payload: H::Payload,
        extensions: Extensions,
        global_ctx: &GlobalContext,
    ) -> PipelineResult {
        let hook_type = HookType::new(H::NAME);
        let entries = self.registry.entries_for_hook(&hook_type);

        if entries.is_empty() {
            let boxed: Box<dyn PluginPayload> = Box::new(payload);
            return PipelineResult::allowed_with(boxed, extensions);
        }

        let boxed: Box<dyn PluginPayload> = Box::new(payload);
        self.executor
            .execute(entries, boxed, extensions, global_ctx)
            .await
    }

    // -----------------------------------------------------------------------
    // Query Methods
    // -----------------------------------------------------------------------

    /// Whether any plugins are registered for the given hook name.
    pub fn has_hooks_for(&self, hook_name: &str) -> bool {
        self.registry.has_hooks_for(&HookType::new(hook_name))
    }

    /// Look up a plugin by name.
    pub fn get_plugin(&self, name: &str) -> Option<&PluginRef> {
        self.registry.get(name)
    }

    /// Total number of registered plugins.
    pub fn plugin_count(&self) -> usize {
        self.registry.plugin_count()
    }

    /// All registered plugin names.
    pub fn plugin_names(&self) -> Vec<&str> {
        self.registry.plugin_names()
    }

    /// Whether the manager has been initialized.
    pub fn is_initialized(&self) -> bool {
        self.initialized
    }

    /// Unregister a plugin by name.
    pub fn unregister(&mut self, name: &str) -> Option<PluginRef> {
        self.registry.unregister(name)
    }
}

impl Default for PluginManager {
    fn default() -> Self {
        Self::new(ManagerConfig::default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::PluginContext;
    use crate::error::PluginViolation;
    use crate::hooks::payload::FilteredExtensions;
    use crate::hooks::{HookHandler, PluginResult};
    use crate::plugin::{OnError, PluginMode};
    use async_trait::async_trait;

    // -- Test payload --

    #[derive(Debug, Clone)]
    struct TestPayload {
        value: String,
    }
    crate::impl_plugin_payload!(TestPayload);

    // -- Test hook type --

    struct TestHook;
    impl HookTypeDef for TestHook {
        type Payload = TestPayload;
        type Result = PluginResult<TestPayload>;
        const NAME: &'static str = "test_hook";
    }

    // -- Test plugins: implement Plugin + HookHandler<TestHook> --
    // No AnyHookHandler boilerplate — the framework handles it.

    /// Plugin that allows everything.
    struct AllowPlugin {
        cfg: PluginConfig,
    }

    #[async_trait]
    impl Plugin for AllowPlugin {
        fn config(&self) -> &PluginConfig { &self.cfg }
        async fn initialize(&self) -> Result<(), PluginError> { Ok(()) }
        async fn shutdown(&self) -> Result<(), PluginError> { Ok(()) }
    }

    impl HookHandler<TestHook> for AllowPlugin {
        fn handle(
            &self,
            _payload: &TestPayload,
            _extensions: &FilteredExtensions,
            _ctx: &PluginContext,
        ) -> PluginResult<TestPayload> {
            PluginResult::allow()
        }
    }

    /// Plugin that denies everything.
    struct DenyPlugin {
        cfg: PluginConfig,
    }

    #[async_trait]
    impl Plugin for DenyPlugin {
        fn config(&self) -> &PluginConfig { &self.cfg }
        async fn initialize(&self) -> Result<(), PluginError> { Ok(()) }
        async fn shutdown(&self) -> Result<(), PluginError> { Ok(()) }
    }

    impl HookHandler<TestHook> for DenyPlugin {
        fn handle(
            &self,
            _payload: &TestPayload,
            _extensions: &FilteredExtensions,
            _ctx: &PluginContext,
        ) -> PluginResult<TestPayload> {
            PluginResult::deny(PluginViolation::new("denied", "test denial"))
        }
    }

    // -- Helper --

    fn make_config(name: &str, priority: i32, mode: PluginMode) -> PluginConfig {
        PluginConfig {
            name: name.to_string(),
            kind: "test".to_string(),
            description: None,
            author: None,
            version: None,
            hooks: vec!["test_hook".to_string()],
            mode,
            priority,
            on_error: OnError::Fail,
            capabilities: Default::default(),
            tags: Vec::new(),
            conditions: Vec::new(),
            config: None,
        }
    }

    // -- Tests --

    #[tokio::test]
    async fn test_manager_lifecycle() {
        let mut mgr = PluginManager::default();
        assert!(!mgr.is_initialized());
        assert_eq!(mgr.plugin_count(), 0);

        mgr.initialize().await.unwrap();
        assert!(mgr.is_initialized());

        // Idempotent
        mgr.initialize().await.unwrap();

        mgr.shutdown().await;
        assert!(!mgr.is_initialized());
    }

    #[tokio::test]
    async fn test_invoke_by_name_no_plugins() {
        let mgr = PluginManager::default();
        let payload: Box<dyn PluginPayload> = Box::new(TestPayload {
            value: "test".into(),
        });
        let ctx = GlobalContext::new("req-1");

        let result = mgr
            .invoke_by_name("test_hook", payload, Extensions::default(), &ctx)
            .await;

        assert!(result.allowed);
        assert!(result.payload.is_some());
    }

    #[tokio::test]
    async fn test_invoke_by_name_allow() {
        let mut mgr = PluginManager::default();
        let config = make_config("allow-plugin", 10, PluginMode::Sequential);
        let plugin = Arc::new(AllowPlugin { cfg: config.clone() });

        // Clean registration — no AnyHookHandler needed
        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();
        mgr.initialize().await.unwrap();

        let payload: Box<dyn PluginPayload> = Box::new(TestPayload {
            value: "test".into(),
        });
        let ctx = GlobalContext::new("req-1");

        let result = mgr
            .invoke_by_name("test_hook", payload, Extensions::default(), &ctx)
            .await;

        assert!(result.allowed);
    }

    #[tokio::test]
    async fn test_invoke_by_name_deny() {
        let mut mgr = PluginManager::default();
        let config = make_config("deny-plugin", 10, PluginMode::Sequential);
        let plugin = Arc::new(DenyPlugin { cfg: config.clone() });

        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();
        mgr.initialize().await.unwrap();

        let payload: Box<dyn PluginPayload> = Box::new(TestPayload {
            value: "test".into(),
        });
        let ctx = GlobalContext::new("req-1");

        let result = mgr
            .invoke_by_name("test_hook", payload, Extensions::default(), &ctx)
            .await;

        assert!(!result.allowed);
        assert_eq!(result.violation.as_ref().unwrap().code, "denied");
    }

    #[tokio::test]
    async fn test_invoke_typed() {
        let mut mgr = PluginManager::default();
        let config = make_config("allow-plugin", 10, PluginMode::Sequential);
        let plugin = Arc::new(AllowPlugin { cfg: config.clone() });

        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();
        mgr.initialize().await.unwrap();

        let payload = TestPayload {
            value: "typed".into(),
        };
        let ctx = GlobalContext::new("req-1");

        let result = mgr
            .invoke::<TestHook>(payload, Extensions::default(), &ctx)
            .await;

        assert!(result.allowed);
    }

    #[tokio::test]
    async fn test_has_hooks_for() {
        let mut mgr = PluginManager::default();
        assert!(!mgr.has_hooks_for("test_hook"));

        let config = make_config("p1", 10, PluginMode::Sequential);
        let plugin = Arc::new(AllowPlugin { cfg: config.clone() });
        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();

        assert!(mgr.has_hooks_for("test_hook"));
        assert!(!mgr.has_hooks_for("other_hook"));
    }

    #[tokio::test]
    async fn test_unregister() {
        let mut mgr = PluginManager::default();
        let config = make_config("removable", 10, PluginMode::Sequential);
        let plugin = Arc::new(AllowPlugin { cfg: config.clone() });
        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();

        assert_eq!(mgr.plugin_count(), 1);
        mgr.unregister("removable");
        assert_eq!(mgr.plugin_count(), 0);
        assert!(!mgr.has_hooks_for("test_hook"));
    }

    #[tokio::test]
    async fn test_audit_plugin_cannot_block() {
        let mut mgr = PluginManager::default();
        let config = make_config("audit-denier", 10, PluginMode::Audit);
        let plugin = Arc::new(DenyPlugin { cfg: config.clone() });

        mgr.register_handler::<TestHook, _>(plugin, config).unwrap();
        mgr.initialize().await.unwrap();

        let payload: Box<dyn PluginPayload> = Box::new(TestPayload {
            value: "test".into(),
        });
        let ctx = GlobalContext::new("req-1");

        let result = mgr
            .invoke_by_name("test_hook", payload, Extensions::default(), &ctx)
            .await;

        // Audit mode — deny is suppressed, pipeline continues
        assert!(result.allowed);
    }
}
