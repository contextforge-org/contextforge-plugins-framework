// Location: ./crates/cpex-ffi/src/lib.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// CPEX FFI — C API for embedding the CPEX runtime.
//
// Exports extern "C" functions that Go (via cgo), Python (via ctypes/cffi),
// and other languages can call. Payloads and extensions cross the boundary
// as MessagePack bytes. ContextTable and BackgroundTasks are opaque handles.
//
// Each PluginManager owns its own tokio runtime so async plugin execution
// works from synchronous cgo calls.

use std::os::raw::{c_char, c_int};
use std::ptr;

use cpex_core::context::PluginContextTable;
use cpex_core::executor::BackgroundTasks;
use cpex_core::extensions::Extensions;
use cpex_core::hooks::payload::PluginPayload;
use cpex_core::manager::PluginManager;

// ---------------------------------------------------------------------------
// Payload Type Registry
// ---------------------------------------------------------------------------

/// Payload type IDs — must match Go constants.
pub const PAYLOAD_GENERIC: u8 = 0;
pub const PAYLOAD_CMF_MESSAGE: u8 = 1;

/// Deserialize a MessagePack payload based on its type ID.
/// Array-indexed — O(1) lookup, zero allocation.
fn deserialize_payload(
    payload_type: u8,
    bytes: &[u8],
) -> Result<Box<dyn PluginPayload>, String> {
    match payload_type {
        PAYLOAD_GENERIC => {
            let value: serde_json::Value = rmp_serde::from_slice(bytes)
                .map_err(|e| format!("generic payload deserialize failed: {}", e))?;
            Ok(Box::new(GenericPayload { value }))
        }
        PAYLOAD_CMF_MESSAGE => {
            let msg: cpex_core::cmf::MessagePayload = rmp_serde::from_slice(bytes)
                .map_err(|e| format!("CMF payload deserialize failed: {}", e))?;
            Ok(Box::new(msg))
        }
        _ => Err(format!("unknown payload type: {}", payload_type)),
    }
}

/// Serialize a modified payload back to MessagePack bytes.
/// Returns the payload type ID alongside the bytes so the caller
/// knows how to deserialize on the other side.
fn serialize_payload(payload: &dyn PluginPayload) -> Option<(u8, Vec<u8>)> {
    // Try CMF MessagePayload first (most common)
    if let Some(mp) = payload.as_any().downcast_ref::<cpex_core::cmf::MessagePayload>() {
        return rmp_serde::to_vec_named(mp).ok().map(|b| (PAYLOAD_CMF_MESSAGE, b));
    }
    // Try GenericPayload
    if let Some(gp) = payload.as_any().downcast_ref::<GenericPayload>() {
        return rmp_serde::to_vec_named(&gp.value).ok().map(|b| (PAYLOAD_GENERIC, b));
    }
    tracing::warn!("serialize_payload: unknown payload type, cannot serialize");
    None
}

// ---------------------------------------------------------------------------
// Opaque Handle Types
// ---------------------------------------------------------------------------

/// Opaque handle to a PluginManager + its tokio runtime.
pub struct CpexManagerInner {
    pub manager: PluginManager,
    pub runtime: tokio::runtime::Runtime,
}

/// Opaque handle to a ContextTable (Rust-owned, not serialized).
pub struct CpexContextTableInner {
    table: PluginContextTable,
}

/// Opaque handle to BackgroundTasks (Rust-owned, not serialized).
pub struct CpexBackgroundTasksInner {
    tasks: BackgroundTasks,
}

// ---------------------------------------------------------------------------
// Helper: safe string from C
// ---------------------------------------------------------------------------

unsafe fn c_str_to_slice<'a>(ptr: *const c_char, len: c_int) -> Option<&'a str> {
    if ptr.is_null() || len <= 0 {
        return None;
    }
    let bytes = std::slice::from_raw_parts(ptr as *const u8, len as usize);
    std::str::from_utf8(bytes).ok()
}

unsafe fn c_bytes_to_slice<'a>(ptr: *const u8, len: c_int) -> Option<&'a [u8]> {
    if ptr.is_null() || len <= 0 {
        return None;
    }
    Some(std::slice::from_raw_parts(ptr, len as usize))
}

/// Allocate a byte buffer and return it to the caller.
/// The caller must free it with `cpex_free_bytes`.
fn alloc_bytes(data: &[u8]) -> (*mut u8, c_int) {
    let len = data.len();
    let layout = std::alloc::Layout::from_size_align(len, 1).unwrap();
    unsafe {
        let ptr = std::alloc::alloc(layout);
        if ptr.is_null() {
            return (ptr::null_mut(), 0);
        }
        std::ptr::copy_nonoverlapping(data.as_ptr(), ptr, len);
        (ptr, len as c_int)
    }
}

// ---------------------------------------------------------------------------
// Manager Lifecycle
// ---------------------------------------------------------------------------

/// Create a new PluginManager from a YAML config string.
///
/// Returns an opaque handle. The manager owns a tokio runtime for
/// async plugin execution. Returns NULL on failure.
///
/// # Safety
/// `config_yaml` must be a valid pointer to `config_len` bytes of UTF-8.
#[no_mangle]
pub unsafe extern "C" fn cpex_manager_new(
    config_yaml: *const c_char,
    config_len: c_int,
) -> *mut CpexManagerInner {
    let yaml = match c_str_to_slice(config_yaml, config_len) {
        Some(s) => s,
        None => return ptr::null_mut(),
    };

    let cpex_config = match cpex_core::config::parse_config(yaml) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("cpex_manager_new: config parse failed: {}", e);
            return ptr::null_mut();
        }
    };

    // Create a per-manager tokio runtime
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            tracing::error!("cpex_manager_new: failed to create tokio runtime: {}", e);
            return ptr::null_mut();
        }
    };

    let mut manager = PluginManager::default();

    // Load config — factories must be registered separately via cpex_register_factory
    if let Err(e) = manager.load_config(cpex_config) {
        tracing::error!("cpex_manager_new: load_config failed: {}", e);
        return ptr::null_mut();
    }

    Box::into_raw(Box::new(CpexManagerInner { manager, runtime }))
}

/// Create a new PluginManager with default config (no YAML).
///
/// Useful when registering plugins programmatically.
#[no_mangle]
pub extern "C" fn cpex_manager_new_default() -> *mut CpexManagerInner {
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .worker_threads(4)
        .enable_all()
        .build()
    {
        Ok(rt) => rt,
        Err(e) => {
            tracing::error!("cpex_manager_new_default: failed to create tokio runtime: {}", e);
            return ptr::null_mut();
        }
    };

    let manager = PluginManager::default();
    Box::into_raw(Box::new(CpexManagerInner { manager, runtime }))
}

/// Load a YAML config into an existing manager.
///
/// Factories must be registered before calling this function.
/// Returns 0 on success, -1 on failure.
///
/// # Safety
/// `mgr` must be a valid handle. `config_yaml` must be valid UTF-8.
#[no_mangle]
pub unsafe extern "C" fn cpex_load_config(
    mgr: *mut CpexManagerInner,
    config_yaml: *const c_char,
    config_len: c_int,
) -> c_int {
    let inner = match mgr.as_mut() {
        Some(m) => m,
        None => return -1,
    };

    let yaml = match c_str_to_slice(config_yaml, config_len) {
        Some(s) => s,
        None => return -1,
    };

    let cpex_config = match cpex_core::config::parse_config(yaml) {
        Ok(c) => c,
        Err(e) => {
            tracing::error!("cpex_load_config: config parse failed: {}", e);
            return -1;
        }
    };

    if let Err(e) = inner.manager.load_config(cpex_config) {
        tracing::error!("cpex_load_config: load_config failed: {}", e);
        return -1;
    }

    0
}

/// Initialize all registered plugins.
///
/// Returns 0 on success, -1 on failure.
///
/// # Safety
/// `mgr` must be a valid handle from `cpex_manager_new`.
#[no_mangle]
pub unsafe extern "C" fn cpex_initialize(mgr: *mut CpexManagerInner) -> c_int {
    let inner = match mgr.as_mut() {
        Some(m) => m,
        None => return -1,
    };

    let result = inner.runtime.block_on(inner.manager.initialize());
    match result {
        Ok(()) => 0,
        Err(e) => {
            tracing::error!("cpex_initialize: {}", e);
            -1
        }
    }
}

/// Shutdown all plugins and free the manager.
///
/// # Safety
/// `mgr` must be a valid handle from `cpex_manager_new`. After this
/// call, the handle is invalid and must not be used.
#[no_mangle]
pub unsafe extern "C" fn cpex_shutdown(mgr: *mut CpexManagerInner) {
    if mgr.is_null() {
        return;
    }
    let mut inner = Box::from_raw(mgr);
    inner.runtime.block_on(inner.manager.shutdown());
    // inner is dropped here, freeing the manager and runtime
}

/// Check if any plugins are registered for a hook name.
///
/// Returns 1 (true) or 0 (false). No serialization — just a hash lookup.
///
/// # Safety
/// `mgr` must be valid. `hook_name` must point to `hook_len` bytes of UTF-8.
#[no_mangle]
pub unsafe extern "C" fn cpex_has_hooks_for(
    mgr: *const CpexManagerInner,
    hook_name: *const c_char,
    hook_len: c_int,
) -> c_int {
    let inner = match mgr.as_ref() {
        Some(m) => m,
        None => return 0,
    };
    let name = match c_str_to_slice(hook_name, hook_len) {
        Some(s) => s,
        None => return 0,
    };
    if inner.manager.has_hooks_for(name) { 1 } else { 0 }
}

/// Get the number of registered plugins.
///
/// No serialization — returns an integer directly.
///
/// # Safety
/// `mgr` must be valid.
#[no_mangle]
pub unsafe extern "C" fn cpex_plugin_count(mgr: *const CpexManagerInner) -> c_int {
    match mgr.as_ref() {
        Some(m) => m.manager.plugin_count() as c_int,
        None => 0,
    }
}

// ---------------------------------------------------------------------------
// Hook Invocation
// ---------------------------------------------------------------------------

/// Invoke a hook by name.
///
/// Payload and extensions are passed as MessagePack bytes.
/// ContextTable is an opaque handle (NULL for first invocation).
/// Returns MessagePack-encoded PipelineResult + opaque handles for
/// context table and background tasks.
///
/// Returns 0 on success, -1 on failure.
///
/// # Safety
/// All pointer parameters must be valid or NULL where documented.
#[no_mangle]
pub unsafe extern "C" fn cpex_invoke(
    mgr: *mut CpexManagerInner,
    hook_name: *const c_char,
    hook_len: c_int,
    payload_type: u8,
    payload_msgpack: *const u8,
    payload_len: c_int,
    extensions_msgpack: *const u8,
    extensions_len: c_int,
    context_table: *mut CpexContextTableInner, // NULL for first call
    result_msgpack_out: *mut *mut u8,
    result_len_out: *mut c_int,
    context_table_out: *mut *mut CpexContextTableInner,
    bg_handle_out: *mut *mut CpexBackgroundTasksInner,
) -> c_int {
    // Validate manager handle
    let inner = match mgr.as_mut() {
        Some(m) => m,
        None => return -1,
    };

    // Parse hook name
    let name = match c_str_to_slice(hook_name, hook_len) {
        Some(s) => s,
        None => return -1,
    };

    // Deserialize payload using the type registry
    let payload_bytes = match c_bytes_to_slice(payload_msgpack, payload_len) {
        Some(b) => b,
        None => return -1,
    };

    let payload: Box<dyn PluginPayload> = match deserialize_payload(payload_type, payload_bytes) {
        Ok(p) => p,
        Err(e) => {
            tracing::error!("cpex_invoke: {}", e);
            return -1;
        }
    };

    // Deserialize extensions from MessagePack
    let extensions: Extensions = if extensions_len > 0 {
        let ext_bytes = match c_bytes_to_slice(extensions_msgpack, extensions_len) {
            Some(b) => b,
            None => return -1,
        };
        match rmp_serde::from_slice(ext_bytes) {
            Ok(e) => e,
            Err(e) => {
                tracing::error!("cpex_invoke: extensions deserialize failed: {}", e);
                return -1;
            }
        }
    } else {
        Extensions::default()
    };

    // Get or create context table
    let ctx_table: Option<PluginContextTable> = if context_table.is_null() {
        None
    } else {
        let ct = Box::from_raw(context_table);
        Some(ct.table)
    };

    // Invoke the hook on the tokio runtime
    let (result, bg) = inner.runtime.block_on(
        inner.manager.invoke_by_name(name, payload, extensions, ctx_table)
    );

    // Serialize modified payload using the type registry
    let (result_payload_type, modified_payload_bytes) = result.modified_payload
        .as_ref()
        .and_then(|p| serialize_payload(p.as_ref()))
        .map(|(t, b)| (t, Some(b)))
        .unwrap_or((payload_type, None)); // preserve original type if no modification

    // Serialize modified extensions if present
    let modified_extensions_bytes: Option<Vec<u8>> = result.modified_extensions
        .as_ref()
        .and_then(|ext| rmp_serde::to_vec_named(ext).ok());

    // Build FFI result
    let ffi_result = FfiPipelineResult {
        continue_processing: result.continue_processing,
        violation: result.violation,
        metadata: result.metadata,
        payload_type: result_payload_type,
        modified_payload: modified_payload_bytes,
        modified_extensions: modified_extensions_bytes,
    };

    let result_bytes = match rmp_serde::to_vec_named(&ffi_result) {
        Ok(b) => b,
        Err(e) => {
            tracing::error!("cpex_invoke: result serialize failed: {}", e);
            return -1;
        }
    };

    // Return result bytes
    let (ptr, len) = alloc_bytes(&result_bytes);
    *result_msgpack_out = ptr;
    *result_len_out = len;

    // Return context table as opaque handle
    *context_table_out = Box::into_raw(Box::new(CpexContextTableInner {
        table: result.context_table,
    }));

    // Return background tasks as opaque handle
    *bg_handle_out = Box::into_raw(Box::new(CpexBackgroundTasksInner {
        tasks: bg,
    }));

    0
}

// ---------------------------------------------------------------------------
// Background Tasks
// ---------------------------------------------------------------------------

/// Wait for all background tasks to complete.
///
/// Returns MessagePack-encoded errors (empty array if none).
/// Returns 0 on success, -1 on failure.
///
/// # Safety
/// `bg_handle` must be a valid handle from `cpex_invoke`.
/// After this call, the handle is consumed and invalid.
#[no_mangle]
pub unsafe extern "C" fn cpex_wait_background(
    mgr: *mut CpexManagerInner,
    bg_handle: *mut CpexBackgroundTasksInner,
    errors_msgpack_out: *mut *mut u8,
    errors_len_out: *mut c_int,
) -> c_int {
    let inner = match mgr.as_mut() {
        Some(m) => m,
        None => return -1,
    };

    if bg_handle.is_null() {
        let (ptr, len) = alloc_bytes(&rmp_serde::to_vec_named(&Vec::<String>::new()).unwrap());
        *errors_msgpack_out = ptr;
        *errors_len_out = len;
        return 0;
    }

    let bg = Box::from_raw(bg_handle);
    let errors = inner.runtime.block_on(bg.tasks.wait_for_background_tasks());

    let error_strings: Vec<String> = errors.iter().map(|e| format!("{}", e)).collect();
    let error_bytes = match rmp_serde::to_vec_named(&error_strings) {
        Ok(b) => b,
        Err(_) => return -1,
    };

    let (ptr, len) = alloc_bytes(&error_bytes);
    *errors_msgpack_out = ptr;
    *errors_len_out = len;

    0
}

/// Free a background tasks handle without waiting.
///
/// Tasks continue running in the tokio runtime.
///
/// # Safety
/// `bg_handle` must be valid or NULL.
#[no_mangle]
pub unsafe extern "C" fn cpex_free_background(bg_handle: *mut CpexBackgroundTasksInner) {
    if !bg_handle.is_null() {
        drop(Box::from_raw(bg_handle));
    }
}

// ---------------------------------------------------------------------------
// Context Table
// ---------------------------------------------------------------------------

/// Free a context table handle.
///
/// # Safety
/// `ct` must be valid or NULL.
#[no_mangle]
pub unsafe extern "C" fn cpex_free_context_table(ct: *mut CpexContextTableInner) {
    if !ct.is_null() {
        drop(Box::from_raw(ct));
    }
}

// ---------------------------------------------------------------------------
// Memory Management
// ---------------------------------------------------------------------------

/// Free a byte buffer allocated by the FFI layer.
///
/// # Safety
/// `ptr` must have been allocated by this library (from `cpex_invoke`
/// or `cpex_wait_background`). `len` must match the original allocation.
#[no_mangle]
pub unsafe extern "C" fn cpex_free_bytes(ptr: *mut u8, len: c_int) {
    if ptr.is_null() || len <= 0 {
        return;
    }
    let layout = std::alloc::Layout::from_size_align(len as usize, 1).unwrap();
    std::alloc::dealloc(ptr, layout);
}

// ---------------------------------------------------------------------------
// FFI Result Types — serialized to MessagePack for the caller
// ---------------------------------------------------------------------------

/// Pipeline result serialized across the FFI boundary.
/// Matches the Go `PipelineResult` struct field names.
#[derive(serde::Serialize, serde::Deserialize)]
struct FfiPipelineResult {
    continue_processing: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    violation: Option<cpex_core::error::PluginViolation>,
    #[serde(skip_serializing_if = "Option::is_none")]
    metadata: Option<serde_json::Value>,
    /// Payload type ID — tells the Go caller how to deserialize.
    payload_type: u8,
    /// Modified payload as raw MessagePack bytes (if a plugin modified it).
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(with = "serde_bytes_opt")]
    modified_payload: Option<Vec<u8>>,
    /// Modified extensions as raw MessagePack bytes (if a plugin modified them).
    #[serde(skip_serializing_if = "Option::is_none")]
    #[serde(with = "serde_bytes_opt")]
    modified_extensions: Option<Vec<u8>>,
}

/// Helper for serializing Option<Vec<u8>> as binary in MessagePack.
mod serde_bytes_opt {
    use serde::{Deserializer, Serializer};

    pub fn serialize<S: Serializer>(v: &Option<Vec<u8>>, s: S) -> Result<S::Ok, S::Error> {
        match v {
            Some(bytes) => serde::Serialize::serialize(&serde_bytes::Bytes::new(bytes), s),
            None => s.serialize_none(),
        }
    }

    pub fn deserialize<'de, D: Deserializer<'de>>(d: D) -> Result<Option<Vec<u8>>, D::Error> {
        use serde::Deserialize;
        Option::<serde_bytes::ByteBuf>::deserialize(d).map(|o| o.map(|b| b.into_vec()))
    }
}

// ---------------------------------------------------------------------------
// Generic Payload — wraps a deserialized MessagePack value
// ---------------------------------------------------------------------------

/// A generic payload that wraps a deserialized serde_json::Value.
///
/// Used for FFI dispatch when the concrete payload type isn't known
/// at compile time. The value was deserialized from MessagePack on
/// the Go side and will be passed to Rust plugins as-is.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct GenericPayload {
    pub value: serde_json::Value,
}

cpex_core::impl_plugin_payload!(GenericPayload);
