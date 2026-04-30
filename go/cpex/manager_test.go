// Location: ./go/cpex/manager_test.go
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// Tests for the CPEX Go SDK.
//
// These tests run against the real Rust runtime via cgo. The
// libcpex_ffi staticlib must be built before running:
//
//	cargo build --release -p cpex-ffi
//	go test -v ./...

package cpex

import (
	"testing"

	"github.com/vmihailenco/msgpack/v5"
)

func TestNewPluginManagerDefault(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if mgr.PluginCount() != 0 {
		t.Errorf("expected 0 plugins, got %d", mgr.PluginCount())
	}

	if mgr.HasHooksFor("test_hook") {
		t.Error("expected no hooks registered")
	}
}

func TestNewPluginManagerFromYAML(t *testing.T) {
	yaml := `
plugin_settings:
  plugin_timeout: 30
`
	mgr, err := NewPluginManager(yaml)
	if err != nil {
		t.Fatalf("NewPluginManager failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	if mgr.PluginCount() != 0 {
		t.Errorf("expected 0 plugins, got %d", mgr.PluginCount())
	}
}

func TestNewPluginManagerInvalidYAML(t *testing.T) {
	_, err := NewPluginManager("not: [valid: yaml: {{}")
	if err == nil {
		t.Error("expected error for invalid YAML")
	}
}

func TestInvokeByNameNoPlugins(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	// Invoke with no registered plugins — should return allowed
	payload := map[string]any{
		"tool_name": "test_tool",
		"user":      "alice",
	}

	ext := &Extensions{
		Meta: &MetaExtension{
			EntityType: "tool",
			EntityName: "test_tool",
		},
	}

	result, ctxTable, bg, err := mgr.InvokeByName("test_hook", PayloadGeneric, payload, ext, nil)
	if err != nil {
		t.Fatalf("InvokeByName failed: %v", err)
	}
	defer ctxTable.Close()
	defer bg.Close()

	if result.IsDenied() {
		t.Error("expected allowed result with no plugins")
	}

	if !result.ContinueProcessing {
		t.Error("expected continue_processing=true")
	}
}

func TestInvokeByNameWithContextTableThreading(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	payload := map[string]any{"tool_name": "test"}
	ext := &Extensions{}

	// First invocation — nil context table
	result1, ctxTable1, bg1, err := mgr.InvokeByName("hook1", PayloadGeneric, payload, ext, nil)
	if err != nil {
		t.Fatalf("first invoke failed: %v", err)
	}
	bg1.Close()

	if result1.IsDenied() {
		t.Error("first invoke should be allowed")
	}

	// Second invocation — thread context table from first
	result2, ctxTable2, bg2, err := mgr.InvokeByName("hook2", PayloadGeneric, payload, ext, ctxTable1)
	if err != nil {
		t.Fatalf("second invoke failed: %v", err)
	}
	bg2.Close()

	if result2.IsDenied() {
		t.Error("second invoke should be allowed")
	}

	ctxTable2.Close()
}

func TestBackgroundTasksWait(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	payload := map[string]any{"test": true}

	result, ctxTable, bg, err := mgr.InvokeByName("test", PayloadGeneric, payload, nil, nil)
	if err != nil {
		t.Fatalf("invoke failed: %v", err)
	}
	defer ctxTable.Close()

	_ = result

	// Wait should return with no errors (no plugins to run)
	errors := bg.Wait()
	if len(errors) > 0 {
		t.Errorf("expected no background errors, got: %v", errors)
	}
}

func TestPluginManagerDoubleShutdown(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}

	mgr.Shutdown()
	// Second shutdown should not panic
	mgr.Shutdown()
}

func TestContextTableDoubleClose(t *testing.T) {
	ct := &ContextTable{}
	ct.Close() // should not panic
	ct.Close() // should not panic
}

func TestBackgroundTasksDoubleClose(t *testing.T) {
	bg := &BackgroundTasks{}
	bg.Close() // should not panic
	bg.Close() // should not panic
}

func TestPipelineResultIsDenied(t *testing.T) {
	allowed := PipelineResult{ContinueProcessing: true}
	if allowed.IsDenied() {
		t.Error("expected not denied")
	}

	denied := PipelineResult{
		ContinueProcessing: false,
		Violation: &PluginViolation{
			Code:   "test_denied",
			Reason: "test reason",
		},
	}
	if !denied.IsDenied() {
		t.Error("expected denied")
	}
}

func TestExtensionsSerialization(t *testing.T) {
	ext := Extensions{
		Meta: &MetaExtension{
			EntityType: "tool",
			EntityName: "get_compensation",
			Tags:       []string{"pii", "hr"},
		},
		Security: &SecurityExtension{
			Labels:         []string{"PII"},
			Classification: "confidential",
			Agent: &AgentIdentity{
				ClientID:    "hr-agent",
				WorkloadID:  "spiffe://corp.com/hr-agent",
				TrustDomain: "corp.com",
			},
		},
		Http: &HttpExtension{
			RequestHeaders: map[string]string{
				"Authorization": "Bearer tok",
				"X-Request-ID":  "req-123",
			},
		},
	}

	// Verify it can be marshaled without error
	_, err := msgpackMarshal(ext)
	if err != nil {
		t.Fatalf("extensions marshal failed: %v", err)
	}
}

// msgpackMarshal is a helper that imports msgpack for the test
func msgpackMarshal(v any) ([]byte, error) {
	return msgpack.Marshal(v)
}

// ---------------------------------------------------------------------------
// Typed Invoke Tests
// ---------------------------------------------------------------------------

func TestInvokeTypedGenericPayload(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	payload := map[string]any{
		"tool_name": "test_tool",
		"user":      "alice",
	}

	result, ct, bg, err := Invoke[map[string]any](
		mgr, "test_hook", PayloadGeneric, payload, &Extensions{}, nil,
	)
	if err != nil {
		t.Fatalf("Invoke failed: %v", err)
	}
	defer ct.Close()
	defer bg.Close()

	if result.IsDenied() {
		t.Error("expected allowed result")
	}

	if !result.ContinueProcessing {
		t.Error("expected continue_processing=true")
	}
}

func TestInvokeTypedCMFPayload(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	msg := MessagePayload{
		Message: NewMessage("assistant",
			TextContent("Looking up compensation data"),
			ToolCallContent(ToolCall{
				ToolCallID: "tc_001",
				Name:       "get_compensation",
				Arguments:  map[string]any{"employee_id": 42},
			}),
		),
	}

	ext := &Extensions{
		Meta: &MetaExtension{
			EntityType: "tool",
			EntityName: "get_compensation",
			Tags:       []string{"pii"},
		},
	}

	result, ct, bg, err := Invoke[MessagePayload](
		mgr, "cmf.tool_pre_invoke", PayloadCMFMessage, msg, ext, nil,
	)
	if err != nil {
		t.Fatalf("Invoke failed: %v", err)
	}
	defer ct.Close()
	defer bg.Close()

	if result.IsDenied() {
		t.Error("expected allowed with no plugins")
	}
}

func TestInvokeTypedContextThreading(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}

	payload := map[string]any{"tool_name": "test"}

	// First call — nil context table
	r1, ct1, bg1, err := Invoke[map[string]any](
		mgr, "hook1", PayloadGeneric, payload, &Extensions{}, nil,
	)
	if err != nil {
		t.Fatalf("first invoke failed: %v", err)
	}
	bg1.Close()

	if r1.IsDenied() {
		t.Error("first invoke should be allowed")
	}

	// Second call — thread context table
	r2, ct2, bg2, err := Invoke[map[string]any](
		mgr, "hook2", PayloadGeneric, payload, &Extensions{}, ct1,
	)
	if err != nil {
		t.Fatalf("second invoke failed: %v", err)
	}
	bg2.Close()

	if r2.IsDenied() {
		t.Error("second invoke should be allowed")
	}

	ct2.Close()
}

func TestTypedPipelineResultIsDenied(t *testing.T) {
	allowed := TypedPipelineResult[map[string]any]{ContinueProcessing: true}
	if allowed.IsDenied() {
		t.Error("expected not denied")
	}

	denied := TypedPipelineResult[map[string]any]{
		ContinueProcessing: false,
		Violation: &PluginViolation{
			Code:   "test",
			Reason: "denied",
		},
	}
	if !denied.IsDenied() {
		t.Error("expected denied")
	}
}

// ---------------------------------------------------------------------------
// CMF Content Part Tests
// ---------------------------------------------------------------------------

func TestContentPartTextRoundTrip(t *testing.T) {
	part := TextContent("hello world")

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "text" {
		t.Errorf("expected content_type=text, got %s", decoded.ContentType)
	}
	if decoded.Text != "hello world" {
		t.Errorf("expected text='hello world', got '%s'", decoded.Text)
	}
}

func TestContentPartThinkingRoundTrip(t *testing.T) {
	part := ThinkingContent("let me analyze...")

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "thinking" {
		t.Errorf("expected content_type=thinking, got %s", decoded.ContentType)
	}
	if decoded.Text != "let me analyze..." {
		t.Errorf("expected thinking text, got '%s'", decoded.Text)
	}
}

func TestContentPartToolCallRoundTrip(t *testing.T) {
	part := ToolCallContent(ToolCall{
		ToolCallID: "tc_001",
		Name:       "get_weather",
		Arguments:  map[string]any{"city": "London"},
		Namespace:  "tools",
	})

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "tool_call" {
		t.Errorf("expected content_type=tool_call, got %s", decoded.ContentType)
	}
	if decoded.ToolCallContent == nil {
		t.Fatal("expected ToolCallContent to be set")
	}
	if decoded.ToolCallContent.Name != "get_weather" {
		t.Errorf("expected name=get_weather, got %s", decoded.ToolCallContent.Name)
	}
	if decoded.ToolCallContent.ToolCallID != "tc_001" {
		t.Errorf("expected tool_call_id=tc_001, got %s", decoded.ToolCallContent.ToolCallID)
	}
	if decoded.ToolCallContent.Namespace != "tools" {
		t.Errorf("expected namespace=tools, got %s", decoded.ToolCallContent.Namespace)
	}
}

func TestContentPartToolResultRoundTrip(t *testing.T) {
	part := ToolResultContent(ToolResult{
		ToolCallID: "tc_001",
		ToolName:   "get_weather",
		Content:    map[string]any{"temp": 20, "unit": "C"},
		IsError:    false,
	})

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "tool_result" {
		t.Errorf("expected content_type=tool_result, got %s", decoded.ContentType)
	}
	if decoded.ToolResultContent == nil {
		t.Fatal("expected ToolResultContent to be set")
	}
	if decoded.ToolResultContent.ToolName != "get_weather" {
		t.Errorf("expected tool_name=get_weather, got %s", decoded.ToolResultContent.ToolName)
	}
}

func TestContentPartResourceRoundTrip(t *testing.T) {
	part := ResourceContent(Resource{
		ResourceRequestID: "rr_001",
		URI:               "file:///data.txt",
		ResourceType:      "file",
		Content:           "Hello from file",
		MimeType:          "text/plain",
	})

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "resource" {
		t.Errorf("expected content_type=resource, got %s", decoded.ContentType)
	}
	if decoded.ResourceContent == nil {
		t.Fatal("expected ResourceContent to be set")
	}
	if decoded.ResourceContent.URI != "file:///data.txt" {
		t.Errorf("expected uri=file:///data.txt, got %s", decoded.ResourceContent.URI)
	}
	if decoded.ResourceContent.Content != "Hello from file" {
		t.Errorf("expected content='Hello from file', got '%s'", decoded.ResourceContent.Content)
	}
}

func TestContentPartImageRoundTrip(t *testing.T) {
	part := ImageContent(ImageSource{
		SourceType: "url",
		Data:       "https://example.com/photo.jpg",
		MediaType:  "image/jpeg",
	})

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "image" {
		t.Errorf("expected content_type=image, got %s", decoded.ContentType)
	}
	if decoded.ImageContent == nil {
		t.Fatal("expected ImageContent to be set")
	}
	if decoded.ImageContent.SourceType != "url" {
		t.Errorf("expected type=url, got %s", decoded.ImageContent.SourceType)
	}
	if decoded.ImageContent.Data != "https://example.com/photo.jpg" {
		t.Errorf("expected data URL, got %s", decoded.ImageContent.Data)
	}
}

func TestContentPartDocumentRoundTrip(t *testing.T) {
	part := DocumentContent(DocumentSource{
		SourceType: "base64",
		Data:       "dGVzdA==",
		MediaType:  "application/pdf",
		Title:      "Quarterly Report",
	})

	data, err := msgpack.Marshal(part)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded ContentPart
	if err := msgpack.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.ContentType != "document" {
		t.Errorf("expected content_type=document, got %s", decoded.ContentType)
	}
	if decoded.DocumentContent == nil {
		t.Fatal("expected DocumentContent to be set")
	}
	if decoded.DocumentContent.Title != "Quarterly Report" {
		t.Errorf("expected title='Quarterly Report', got '%s'", decoded.DocumentContent.Title)
	}
}

func TestMessagePayloadSerialization(t *testing.T) {
	msg := MessagePayload{
		Message: NewMessage("assistant",
			TextContent("I'll look that up for you."),
			ToolCallContent(ToolCall{
				ToolCallID: "tc_001",
				Name:       "get_compensation",
				Arguments:  map[string]any{"employee_id": 42},
			}),
		),
	}

	data, err := msgpack.Marshal(msg)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	if len(data) == 0 {
		t.Fatal("expected non-empty msgpack bytes")
	}

	// Verify it round-trips as a generic map (to check wire format)
	var raw map[string]any
	if err := msgpack.Unmarshal(data, &raw); err != nil {
		t.Fatalf("unmarshal to map failed: %v", err)
	}

	message, ok := raw["message"].(map[string]any)
	if !ok {
		t.Fatal("expected 'message' key in payload")
	}

	if message["schema_version"] != "2.0" {
		t.Errorf("expected schema_version=2.0, got %v", message["schema_version"])
	}

	if message["role"] != "assistant" {
		t.Errorf("expected role=assistant, got %v", message["role"])
	}

	content, ok := message["content"].([]any)
	if !ok {
		t.Fatal("expected content to be a list")
	}

	if len(content) != 2 {
		t.Fatalf("expected 2 content parts, got %d", len(content))
	}

	// First part should be text
	part0, ok := content[0].(map[string]any)
	if !ok {
		t.Fatal("expected content[0] to be a map")
	}
	if part0["content_type"] != "text" {
		t.Errorf("expected content_type=text, got %v", part0["content_type"])
	}

	// Second part should be tool_call
	part1, ok := content[1].(map[string]any)
	if !ok {
		t.Fatal("expected content[1] to be a map")
	}
	if part1["content_type"] != "tool_call" {
		t.Errorf("expected content_type=tool_call, got %v", part1["content_type"])
	}
}

func TestLoadConfigOnDefaultManager(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	// LoadConfig with valid YAML (no plugins, just settings)
	err = mgr.LoadConfig(`
plugin_settings:
  plugin_timeout: 30
`)
	if err != nil {
		t.Fatalf("LoadConfig failed: %v", err)
	}

	if err := mgr.Initialize(); err != nil {
		t.Fatalf("Initialize failed: %v", err)
	}
}

func TestLoadConfigInvalidYAML(t *testing.T) {
	mgr, err := NewPluginManagerDefault()
	if err != nil {
		t.Fatalf("NewPluginManagerDefault failed: %v", err)
	}
	defer mgr.Shutdown()

	err = mgr.LoadConfig("not: [valid: yaml: {{}")
	if err == nil {
		t.Error("expected error for invalid YAML")
	}
}
