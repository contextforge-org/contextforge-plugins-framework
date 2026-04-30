// Location: ./go/cpex/manager.go
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// PluginManager — Go wrapper for the CPEX plugin runtime.
//
// Owns the lifecycle of the Rust PluginManager via cgo. Provides
// the public API that Go host systems call to register factories,
// load config, initialize plugins, and invoke hooks.
//
// Lifecycle:
//
//	NewPluginManagerDefault() → RegisterFactories() → LoadConfig() → Initialize() → InvokeByName() → Shutdown()
//
// Payloads and extensions are serialized to MessagePack when
// crossing the FFI boundary. ContextTable and BackgroundTasks
// are opaque handles to Rust-owned data.

package cpex

import (
	"errors"
	"fmt"
	"runtime"
	"unsafe"

	"github.com/vmihailenco/msgpack/v5"
)

/*
#include <stdint.h>
#include <stdlib.h>

// Opaque handles
typedef void* CpexManager;
typedef void* CpexContextTable;
typedef void* CpexBackgroundTasks;

// Extern declarations — implemented in libcpex_ffi
extern CpexManager cpex_manager_new(const char* config_yaml, int config_len);
extern CpexManager cpex_manager_new_default();
extern int cpex_load_config(CpexManager mgr, const char* config_yaml, int config_len);
extern int cpex_initialize(CpexManager mgr);
extern void cpex_shutdown(CpexManager mgr);
extern int cpex_has_hooks_for(CpexManager mgr, const char* hook_name, int hook_len);
extern int cpex_plugin_count(CpexManager mgr);
extern int cpex_invoke(
    CpexManager mgr,
    const char* hook_name, int hook_len,
    uint8_t payload_type,
    const uint8_t* payload_msgpack, int payload_len,
    const uint8_t* extensions_msgpack, int extensions_len,
    CpexContextTable context_table,
    uint8_t** result_msgpack_out, int* result_len_out,
    CpexContextTable* context_table_out,
    CpexBackgroundTasks* bg_handle_out
);
extern int cpex_wait_background(
    CpexManager mgr,
    CpexBackgroundTasks bg_handle,
    uint8_t** errors_msgpack_out, int* errors_len_out
);
extern void cpex_free_background(CpexBackgroundTasks bg_handle);
extern void cpex_free_context_table(CpexContextTable ct);
extern void cpex_free_bytes(uint8_t* ptr, int len);
*/
import "C"

// PluginManager manages the lifecycle of CPEX plugins and hook dispatch.
// Wraps the Rust PluginManager — all plugin execution happens in Rust.
type PluginManager struct {
	handle C.CpexManager
}

// ContextTable holds per-plugin context state across hook invocations.
// Opaque handle to Rust-owned data — not serialized.
type ContextTable struct {
	handle C.CpexContextTable
}

// BackgroundTasks holds fire-and-forget task handles.
// Opaque handle to Rust-owned data — not serialized.
type BackgroundTasks struct {
	handle C.CpexBackgroundTasks
	mgr    C.CpexManager // needed for wait
}

// NewPluginManager creates a manager from a YAML config string.
// Built-in Rust plugin factories are registered automatically.
func NewPluginManager(yaml string) (*PluginManager, error) {
	cYaml := C.CString(yaml)
	defer C.free(unsafe.Pointer(cYaml))

	handle := C.cpex_manager_new(cYaml, C.int(len(yaml)))
	if handle == nil {
		return nil, errors.New("cpex: failed to create plugin manager from config")
	}

	mgr := &PluginManager{handle: handle}
	runtime.SetFinalizer(mgr, func(m *PluginManager) {
		if m.handle != nil {
			C.cpex_shutdown(m.handle)
			m.handle = nil
		}
	})

	return mgr, nil
}

// NewPluginManagerDefault creates a manager with default config.
// Useful when registering plugins programmatically.
func NewPluginManagerDefault() (*PluginManager, error) {
	handle := C.cpex_manager_new_default()
	if handle == nil {
		return nil, errors.New("cpex: failed to create default plugin manager")
	}

	mgr := &PluginManager{handle: handle}
	runtime.SetFinalizer(mgr, func(m *PluginManager) {
		if m.handle != nil {
			C.cpex_shutdown(m.handle)
			m.handle = nil
		}
	})

	return mgr, nil
}

// FactoryRegistrar is a function that registers plugin factories on the
// manager's internal handle. The handle is an opaque C pointer — callers
// pass it to their own extern C registration function.
type FactoryRegistrar func(handle unsafe.Pointer) error

// RegisterFactories calls fn with the manager's internal C handle,
// allowing callers to register plugin factories via their own FFI.
// Must be called before LoadConfig.
func (m *PluginManager) RegisterFactories(fn FactoryRegistrar) error {
	if m.handle == nil {
		return errors.New("cpex: manager is nil")
	}
	return fn(unsafe.Pointer(m.handle))
}

// LoadConfig loads a YAML config string into the manager.
// Factories must be registered before calling this method.
func (m *PluginManager) LoadConfig(yaml string) error {
	if m.handle == nil {
		return errors.New("cpex: manager is nil")
	}

	cYaml := C.CString(yaml)
	defer C.free(unsafe.Pointer(cYaml))

	rc := C.cpex_load_config(m.handle, cYaml, C.int(len(yaml)))
	if rc != 0 {
		return errors.New("cpex: load config failed")
	}
	return nil
}

// Initialize calls Initialize on all registered plugins.
// Must be called before invoking any hooks.
func (m *PluginManager) Initialize() error {
	if m.handle == nil {
		return errors.New("cpex: manager is nil")
	}

	rc := C.cpex_initialize(m.handle)
	if rc != 0 {
		return errors.New("cpex: initialization failed")
	}
	return nil
}

// Shutdown gracefully shuts down all plugins and releases resources.
// After this call, the manager is invalid and must not be used.
func (m *PluginManager) Shutdown() {
	if m.handle == nil {
		return
	}
	C.cpex_shutdown(m.handle)
	m.handle = nil
}

// HasHooksFor returns true if any plugins are registered for the hook.
// No serialization — just a hash lookup across the FFI boundary.
func (m *PluginManager) HasHooksFor(hookName string) bool {
	if m.handle == nil {
		return false
	}
	cName := C.CString(hookName)
	defer C.free(unsafe.Pointer(cName))
	return C.cpex_has_hooks_for(m.handle, cName, C.int(len(hookName))) == 1
}

// PluginCount returns the number of registered plugins.
func (m *PluginManager) PluginCount() int {
	if m.handle == nil {
		return 0
	}
	return int(C.cpex_plugin_count(m.handle))
}

// InvokeByName invokes a hook by name with a payload and extensions.
// Payload and extensions are serialized to MessagePack internally.
// The ContextTable is an opaque handle — pass nil on the first call,
// then thread result's ContextTable into subsequent calls.
func (m *PluginManager) InvokeByName(
	hookName string,
	payloadType uint8,
	payload any,
	extensions *Extensions,
	contextTable *ContextTable,
) (*PipelineResult, *ContextTable, *BackgroundTasks, error) {
	if m.handle == nil {
		return nil, nil, nil, errors.New("cpex: manager is nil")
	}

	// Serialize payload to MessagePack
	payloadBytes, err := msgpack.Marshal(payload)
	if err != nil {
		return nil, nil, nil, fmt.Errorf("cpex: payload marshal failed: %w", err)
	}

	// Serialize extensions to MessagePack
	var extBytes []byte
	if extensions != nil {
		extBytes, err = msgpack.Marshal(extensions)
		if err != nil {
			return nil, nil, nil, fmt.Errorf("cpex: extensions marshal failed: %w", err)
		}
	}

	// Prepare C args
	cHookName := C.CString(hookName)
	defer C.free(unsafe.Pointer(cHookName))

	var ctHandle C.CpexContextTable
	if contextTable != nil {
		ctHandle = contextTable.handle
		contextTable.handle = nil // consumed by Rust
	}

	var resultPtr *C.uint8_t
	var resultLen C.int
	var ctOut C.CpexContextTable
	var bgOut C.CpexBackgroundTasks

	var payloadPtr *C.uint8_t
	if len(payloadBytes) > 0 {
		payloadPtr = (*C.uint8_t)(unsafe.Pointer(&payloadBytes[0]))
	}

	var extPtr *C.uint8_t
	var extLen C.int
	if len(extBytes) > 0 {
		extPtr = (*C.uint8_t)(unsafe.Pointer(&extBytes[0]))
		extLen = C.int(len(extBytes))
	}

	rc := C.cpex_invoke(
		m.handle,
		cHookName, C.int(len(hookName)),
		C.uint8_t(payloadType),
		payloadPtr, C.int(len(payloadBytes)),
		extPtr, extLen,
		ctHandle,
		&resultPtr, &resultLen,
		&ctOut,
		&bgOut,
	)

	if rc != 0 {
		return nil, nil, nil, errors.New("cpex: invoke failed")
	}

	// Deserialize result from MessagePack
	resultBytes := C.GoBytes(unsafe.Pointer(resultPtr), resultLen)
	C.cpex_free_bytes((*C.uint8_t)(unsafe.Pointer(resultPtr)), resultLen)

	var result PipelineResult
	if err := msgpack.Unmarshal(resultBytes, &result); err != nil {
		return nil, nil, nil, fmt.Errorf("cpex: result unmarshal failed: %w", err)
	}

	// Wrap opaque handles
	resultCT := &ContextTable{handle: ctOut}
	runtime.SetFinalizer(resultCT, func(ct *ContextTable) {
		ct.Close()
	})

	bg := &BackgroundTasks{handle: bgOut, mgr: m.handle}

	return &result, resultCT, bg, nil
}

// Invoke is the typed invoke path. Calls InvokeByName and deserializes
// the modified payload and extensions into concrete Go types.
//
// Example:
//
//	result, ct, bg, err := cpex.Invoke[cpex.MessagePayload](
//	    mgr, "cmf.tool_pre_invoke", cpex.PayloadCMFMessage,
//	    payload, ext, nil,
//	)
//	if !result.IsDenied() && result.ModifiedPayload != nil {
//	    fmt.Println(result.ModifiedPayload.Message.Role)
//	}
func Invoke[P any](
	m *PluginManager,
	hookName string,
	payloadType uint8,
	payload P,
	extensions *Extensions,
	contextTable *ContextTable,
) (*TypedPipelineResult[P], *ContextTable, *BackgroundTasks, error) {
	raw, ct, bg, err := m.InvokeByName(hookName, payloadType, payload, extensions, contextTable)
	if err != nil {
		return nil, nil, nil, err
	}

	typed := &TypedPipelineResult[P]{
		ContinueProcessing: raw.ContinueProcessing,
		Violation:          raw.Violation,
		Metadata:           raw.Metadata,
		PayloadType:        raw.PayloadType,
	}

	// Deserialize modified payload if present
	if len(raw.ModifiedPayload) > 0 {
		var v P
		if err := msgpack.Unmarshal(raw.ModifiedPayload, &v); err != nil {
			return nil, ct, bg, fmt.Errorf("cpex: modified payload unmarshal failed: %w", err)
		}
		typed.ModifiedPayload = &v
	}

	// Deserialize modified extensions if present
	if len(raw.ModifiedExtensions) > 0 {
		var ext Extensions
		if err := msgpack.Unmarshal(raw.ModifiedExtensions, &ext); err != nil {
			return nil, ct, bg, fmt.Errorf("cpex: modified extensions unmarshal failed: %w", err)
		}
		typed.ModifiedExtensions = &ext
	}

	return typed, ct, bg, nil
}

// Wait blocks until all background tasks complete.
// Returns errors from any tasks that panicked.
func (bg *BackgroundTasks) Wait() []string {
	if bg.handle == nil || bg.mgr == nil {
		return nil
	}

	var errorsPtr *C.uint8_t
	var errorsLen C.int

	C.cpex_wait_background(bg.mgr, bg.handle, &errorsPtr, &errorsLen)
	bg.handle = nil // consumed

	errorsBytes := C.GoBytes(unsafe.Pointer(errorsPtr), errorsLen)
	C.cpex_free_bytes((*C.uint8_t)(unsafe.Pointer(errorsPtr)), errorsLen)

	var errorStrings []string
	_ = msgpack.Unmarshal(errorsBytes, &errorStrings)
	return errorStrings
}

// Close releases the background task handles without waiting.
// Tasks continue running in the Rust tokio runtime.
func (bg *BackgroundTasks) Close() {
	if bg.handle == nil {
		return
	}
	C.cpex_free_background(bg.handle)
	bg.handle = nil
}

// Close releases the Rust-owned context table.
func (ct *ContextTable) Close() {
	if ct.handle == nil {
		return
	}
	C.cpex_free_context_table(ct.handle)
	ct.handle = nil
}

