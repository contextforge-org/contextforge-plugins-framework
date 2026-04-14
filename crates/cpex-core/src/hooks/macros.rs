// Location: ./crates/cpex-core/src/hooks/macros.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// define_hook! macro.
//
// Generates a HookTypeDef marker struct, trait implementation, and
// a handler trait from a single declaration. This is the primary
// way to define new hooks — both built-in (CMF, tool, prompt) and
// custom (rate limiting, deployment gates, federation sync).
//
// The generated handler trait has a single method whose name is
// derived from the hook name. The handler receives:
//   - payload: the typed payload (owned — executor decides borrow vs clone)
//   - extensions: &FilteredExtensions (capability-gated, separate from payload)
//   - ctx: &PluginContext
//
// And returns the hook's result type (typically PluginResult<Payload>).

/// Generates a hook type definition, marker struct, and handler trait.
///
/// # Usage
///
/// ```rust,ignore
/// define_hook! {
///     /// Doc comment for the hook.
///     MyHook, "my_hook" => {
///         payload: MyPayload,
///         result: PluginResult<MyPayload>,
///     }
/// }
/// ```
///
/// This generates:
///
/// 1. A marker struct `MyHook` implementing `HookTypeDef`.
/// 2. A handler trait `MyHookHandler` with a method `my_hook()`.
///
/// The handler method receives:
/// - `payload: MyPayload` (owned)
/// - `extensions: &FilteredExtensions`
/// - `ctx: &PluginContext`
///
/// And returns `PluginResult<MyPayload>`.
///
/// # CMF Pattern (one handler, multiple hook names)
///
/// For CMF hooks where one handler covers multiple hook names:
///
/// ```rust,ignore
/// define_hook! {
///     /// CMF message evaluation hook.
///     CmfHook, "cmf" => {
///         payload: MessagePayload,
///         result: PluginResult<MessagePayload>,
///     }
/// }
///
/// // Register the same handler for multiple names:
/// // registry.register_for_names::<CmfHook>(plugin, config, &[
/// //     "cmf.tool_pre_invoke", "cmf.llm_input", ...
/// // ]);
/// ```
#[macro_export]
macro_rules! define_hook {
    (
        $(#[$meta:meta])*
        $name:ident, $hook_name:literal => {
            payload: $payload:ty,
            result: $result:ty $(,)?
        }
    ) => {
        $(#[$meta])*
        pub struct $name;

        impl $crate::hooks::trait_def::HookTypeDef for $name {
            type Payload = $payload;
            type Result = $result;
            const NAME: &'static str = $hook_name;
        }

        paste::paste! {
            /// Handler trait for the
            #[doc = concat!("`", stringify!($name), "`")]
            /// hook. Implement this on your plugin to handle this hook type.
            pub trait [<$name Handler>]: $crate::plugin::Plugin + Send + Sync {
                /// Handle the
                #[doc = concat!("`", $hook_name, "`")]
                /// hook.
                ///
                /// The executor decides whether to pass a clone (for Sequential/
                /// Transform modes) or a borrow (for Audit/Concurrent/FireAndForget
                /// modes) based on the plugin's mode. The handler signature always
                /// takes owned payload — the executor handles the mechanics.
                fn [<$hook_name>](
                    &self,
                    payload: $payload,
                    extensions: &$crate::hooks::payload::FilteredExtensions,
                    ctx: &$crate::context::PluginContext,
                ) -> $result;
            }
        }
    };
}
