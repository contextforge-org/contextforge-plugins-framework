// Location: ./go/cpex/cmf.go
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// CMF (ContextForge Message Format) types for Go.
//
// Mirrors the Rust types in crates/cpex-core/src/cmf/. The Message
// struct carries typed content parts (text, tool calls, resources,
// media, etc.) without extensions — those are passed separately.
//
// ContentPart is a tagged union discriminated by the "content_type"
// field. Custom msgpack Encoder/Decoder methods produce the same
// wire format as Rust's #[serde(tag = "content_type")] enum.

package cpex

import "github.com/vmihailenco/msgpack/v5"

// ---------------------------------------------------------------------------
// CMF Message Types
// ---------------------------------------------------------------------------

// MessagePayload wraps a Message for FFI transport.
// Matches Rust's cpex_core::cmf::MessagePayload.
type MessagePayload struct {
	Message Message `msgpack:"message"`
}

// Message is the ContextForge Message Format (CMF) message.
// No extensions — those are passed separately to the plugin pipeline.
type Message struct {
	SchemaVersion string        `msgpack:"schema_version"`
	Role          string        `msgpack:"role"`
	Content       []ContentPart `msgpack:"content"`
	Channel       string        `msgpack:"channel,omitempty"`
}

// NewMessage creates a Message with the default schema version.
func NewMessage(role string, content ...ContentPart) Message {
	return Message{
		SchemaVersion: "2.0",
		Role:          role,
		Content:       content,
	}
}

// ---------------------------------------------------------------------------
// Content Parts — tagged union via content_type discriminator
// ---------------------------------------------------------------------------

// ContentPart represents one element in a Message's content list.
// Uses custom msgpack marshaling to produce the tagged-union wire format:
//
//	{"content_type": "text", "text": "hello"}
//	{"content_type": "tool_call", "content": {...}}
//
// The ContentType field determines which content field is populated.
// Text and Thinking use the Text field directly; all other types use
// their respective content field.
type ContentPart struct {
	ContentType string

	// Text/Thinking — "text" field at top level
	Text string

	// Structured content — "content" field wrapping a domain object.
	// Only one is set based on ContentType.
	ToolCallContent          *ToolCall
	ToolResultContent        *ToolResult
	ResourceContent          *Resource
	ResourceRefContent       *ResourceReference
	PromptRequestContent     *PromptRequest
	PromptResultContent      *PromptResult
	ImageContent             *ImageSource
	VideoContent             *VideoSource
	AudioContent             *AudioSource
	DocumentContent          *DocumentSource
}

// EncodeMsgpack produces the tagged-union wire format.
func (cp ContentPart) EncodeMsgpack(enc *msgpack.Encoder) error {
	switch cp.ContentType {
	case "text", "thinking":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"text":         cp.Text,
		})
	case "tool_call":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.ToolCallContent,
		})
	case "tool_result":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.ToolResultContent,
		})
	case "resource":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.ResourceContent,
		})
	case "resource_ref":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.ResourceRefContent,
		})
	case "prompt_request":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.PromptRequestContent,
		})
	case "prompt_result":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.PromptResultContent,
		})
	case "image":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.ImageContent,
		})
	case "video":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.VideoContent,
		})
	case "audio":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.AudioContent,
		})
	case "document":
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"content":      cp.DocumentContent,
		})
	default:
		// Unknown type — encode as text fallback
		return enc.Encode(map[string]any{
			"content_type": cp.ContentType,
			"text":         cp.Text,
		})
	}
}

// DecodeMsgpack reads the tagged-union wire format.
func (cp *ContentPart) DecodeMsgpack(dec *msgpack.Decoder) error {
	var raw map[string]any
	if err := dec.Decode(&raw); err != nil {
		return err
	}

	if ct, ok := raw["content_type"].(string); ok {
		cp.ContentType = ct
	}

	switch cp.ContentType {
	case "text", "thinking":
		if t, ok := raw["text"].(string); ok {
			cp.Text = t
		}
	case "tool_call":
		cp.ToolCallContent = decodeToolCall(raw["content"])
	case "tool_result":
		cp.ToolResultContent = decodeToolResult(raw["content"])
	case "resource":
		cp.ResourceContent = decodeResource(raw["content"])
	case "resource_ref":
		cp.ResourceRefContent = decodeResourceRef(raw["content"])
	case "prompt_request":
		cp.PromptRequestContent = decodePromptRequest(raw["content"])
	case "prompt_result":
		cp.PromptResultContent = decodePromptResult(raw["content"])
	case "image":
		cp.ImageContent = decodeImageSource(raw["content"])
	case "video":
		cp.VideoContent = decodeVideoSource(raw["content"])
	case "audio":
		cp.AudioContent = decodeAudioSource(raw["content"])
	case "document":
		cp.DocumentContent = decodeDocumentSource(raw["content"])
	}

	return nil
}

// ---------------------------------------------------------------------------
// Content Part Constructors
// ---------------------------------------------------------------------------

// TextContent creates a text content part.
func TextContent(text string) ContentPart {
	return ContentPart{ContentType: "text", Text: text}
}

// ThinkingContent creates a thinking content part.
func ThinkingContent(text string) ContentPart {
	return ContentPart{ContentType: "thinking", Text: text}
}

// ToolCallContent creates a tool_call content part.
func ToolCallContent(tc ToolCall) ContentPart {
	return ContentPart{ContentType: "tool_call", ToolCallContent: &tc}
}

// ToolResultContent creates a tool_result content part.
func ToolResultContent(tr ToolResult) ContentPart {
	return ContentPart{ContentType: "tool_result", ToolResultContent: &tr}
}

// ResourceContent creates a resource content part.
func ResourceContent(r Resource) ContentPart {
	return ContentPart{ContentType: "resource", ResourceContent: &r}
}

// ResourceRefContent creates a resource_ref content part.
func ResourceRefContent(r ResourceReference) ContentPart {
	return ContentPart{ContentType: "resource_ref", ResourceRefContent: &r}
}

// PromptRequestContent creates a prompt_request content part.
func PromptRequestContent(pr PromptRequest) ContentPart {
	return ContentPart{ContentType: "prompt_request", PromptRequestContent: &pr}
}

// PromptResultContent creates a prompt_result content part.
func PromptResultContent(pr PromptResult) ContentPart {
	return ContentPart{ContentType: "prompt_result", PromptResultContent: &pr}
}

// ImageContent creates an image content part.
func ImageContent(img ImageSource) ContentPart {
	return ContentPart{ContentType: "image", ImageContent: &img}
}

// VideoContent creates a video content part.
func VideoContent(vid VideoSource) ContentPart {
	return ContentPart{ContentType: "video", VideoContent: &vid}
}

// AudioContent creates an audio content part.
func AudioContent(aud AudioSource) ContentPart {
	return ContentPart{ContentType: "audio", AudioContent: &aud}
}

// DocumentContent creates a document content part.
func DocumentContent(doc DocumentSource) ContentPart {
	return ContentPart{ContentType: "document", DocumentContent: &doc}
}

// ---------------------------------------------------------------------------
// Domain Objects
// ---------------------------------------------------------------------------

// ToolCall represents a tool invocation request.
type ToolCall struct {
	ToolCallID string         `msgpack:"tool_call_id"`
	Name       string         `msgpack:"name"`
	Arguments  map[string]any `msgpack:"arguments,omitempty"`
	Namespace  string         `msgpack:"namespace,omitempty"`
}

// ToolResult represents the output of a tool execution.
type ToolResult struct {
	ToolCallID string `msgpack:"tool_call_id"`
	ToolName   string `msgpack:"tool_name"`
	Content    any    `msgpack:"content,omitempty"`
	IsError    bool   `msgpack:"is_error,omitempty"`
}

// Resource represents an embedded resource with content (MCP).
type Resource struct {
	ResourceRequestID string         `msgpack:"resource_request_id"`
	URI               string         `msgpack:"uri"`
	Name              string         `msgpack:"name,omitempty"`
	Description       string         `msgpack:"description,omitempty"`
	ResourceType      string         `msgpack:"resource_type"`
	Content           string         `msgpack:"content,omitempty"`
	Blob              []byte         `msgpack:"blob,omitempty"`
	MimeType          string         `msgpack:"mime_type,omitempty"`
	SizeBytes         *uint64        `msgpack:"size_bytes,omitempty"`
	Annotations       map[string]any `msgpack:"annotations,omitempty"`
	Version           string         `msgpack:"version,omitempty"`
}

// ResourceReference is a lightweight resource reference without content.
type ResourceReference struct {
	ResourceRequestID string  `msgpack:"resource_request_id"`
	URI               string  `msgpack:"uri"`
	Name              string  `msgpack:"name,omitempty"`
	ResourceType      string  `msgpack:"resource_type"`
	RangeStart        *uint64 `msgpack:"range_start,omitempty"`
	RangeEnd          *uint64 `msgpack:"range_end,omitempty"`
	Selector          string  `msgpack:"selector,omitempty"`
}

// PromptRequest represents a prompt template invocation request (MCP).
type PromptRequest struct {
	PromptRequestID string         `msgpack:"prompt_request_id"`
	Name            string         `msgpack:"name"`
	Arguments       map[string]any `msgpack:"arguments,omitempty"`
	ServerID        string         `msgpack:"server_id,omitempty"`
}

// PromptResult represents a rendered prompt template result.
type PromptResult struct {
	PromptRequestID string    `msgpack:"prompt_request_id"`
	PromptName      string    `msgpack:"prompt_name"`
	Messages        []Message `msgpack:"messages,omitempty"`
	Content         string    `msgpack:"content,omitempty"`
	IsError         bool      `msgpack:"is_error,omitempty"`
	ErrorMessage    string    `msgpack:"error_message,omitempty"`
}

// ---------------------------------------------------------------------------
// Media Source Types
// ---------------------------------------------------------------------------

// ImageSource holds image data (URL or base64).
type ImageSource struct {
	SourceType string `msgpack:"type"`
	Data       string `msgpack:"data"`
	MediaType  string `msgpack:"media_type,omitempty"`
}

// VideoSource holds video data (URL or base64).
type VideoSource struct {
	SourceType string  `msgpack:"type"`
	Data       string  `msgpack:"data"`
	MediaType  string  `msgpack:"media_type,omitempty"`
	DurationMs *uint64 `msgpack:"duration_ms,omitempty"`
}

// AudioSource holds audio data (URL or base64).
type AudioSource struct {
	SourceType string  `msgpack:"type"`
	Data       string  `msgpack:"data"`
	MediaType  string  `msgpack:"media_type,omitempty"`
	DurationMs *uint64 `msgpack:"duration_ms,omitempty"`
}

// DocumentSource holds document data (URL or base64).
type DocumentSource struct {
	SourceType string `msgpack:"type"`
	Data       string `msgpack:"data"`
	MediaType  string `msgpack:"media_type,omitempty"`
	Title      string `msgpack:"title,omitempty"`
}

// ---------------------------------------------------------------------------
// Decode helpers — extract typed domain objects from map[string]any
// ---------------------------------------------------------------------------

func decodeToolCall(v any) *ToolCall {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	tc := &ToolCall{}
	if id, ok := m["tool_call_id"].(string); ok {
		tc.ToolCallID = id
	}
	if name, ok := m["name"].(string); ok {
		tc.Name = name
	}
	if args, ok := m["arguments"].(map[string]any); ok {
		tc.Arguments = args
	}
	if ns, ok := m["namespace"].(string); ok {
		tc.Namespace = ns
	}
	return tc
}

func decodeToolResult(v any) *ToolResult {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	tr := &ToolResult{}
	if id, ok := m["tool_call_id"].(string); ok {
		tr.ToolCallID = id
	}
	if name, ok := m["tool_name"].(string); ok {
		tr.ToolName = name
	}
	if content, ok := m["content"]; ok {
		tr.Content = content
	}
	if isErr, ok := m["is_error"].(bool); ok {
		tr.IsError = isErr
	}
	return tr
}

func decodeResource(v any) *Resource {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	r := &Resource{}
	if id, ok := m["resource_request_id"].(string); ok {
		r.ResourceRequestID = id
	}
	if uri, ok := m["uri"].(string); ok {
		r.URI = uri
	}
	if name, ok := m["name"].(string); ok {
		r.Name = name
	}
	if desc, ok := m["description"].(string); ok {
		r.Description = desc
	}
	if rt, ok := m["resource_type"].(string); ok {
		r.ResourceType = rt
	}
	if content, ok := m["content"].(string); ok {
		r.Content = content
	}
	if mime, ok := m["mime_type"].(string); ok {
		r.MimeType = mime
	}
	if ann, ok := m["annotations"].(map[string]any); ok {
		r.Annotations = ann
	}
	if ver, ok := m["version"].(string); ok {
		r.Version = ver
	}
	return r
}

func decodeResourceRef(v any) *ResourceReference {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	r := &ResourceReference{}
	if id, ok := m["resource_request_id"].(string); ok {
		r.ResourceRequestID = id
	}
	if uri, ok := m["uri"].(string); ok {
		r.URI = uri
	}
	if name, ok := m["name"].(string); ok {
		r.Name = name
	}
	if rt, ok := m["resource_type"].(string); ok {
		r.ResourceType = rt
	}
	if sel, ok := m["selector"].(string); ok {
		r.Selector = sel
	}
	return r
}

func decodePromptRequest(v any) *PromptRequest {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	pr := &PromptRequest{}
	if id, ok := m["prompt_request_id"].(string); ok {
		pr.PromptRequestID = id
	}
	if name, ok := m["name"].(string); ok {
		pr.Name = name
	}
	if args, ok := m["arguments"].(map[string]any); ok {
		pr.Arguments = args
	}
	if sid, ok := m["server_id"].(string); ok {
		pr.ServerID = sid
	}
	return pr
}

func decodePromptResult(v any) *PromptResult {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	pr := &PromptResult{}
	if id, ok := m["prompt_request_id"].(string); ok {
		pr.PromptRequestID = id
	}
	if name, ok := m["prompt_name"].(string); ok {
		pr.PromptName = name
	}
	if content, ok := m["content"].(string); ok {
		pr.Content = content
	}
	if isErr, ok := m["is_error"].(bool); ok {
		pr.IsError = isErr
	}
	if errMsg, ok := m["error_message"].(string); ok {
		pr.ErrorMessage = errMsg
	}
	// Note: messages ([]Message) decoding is not handled here —
	// PromptResult.Messages containing nested Messages would require
	// recursive decode. For now, leave empty; can be added if needed.
	return pr
}

func decodeImageSource(v any) *ImageSource {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	s := &ImageSource{}
	if t, ok := m["type"].(string); ok {
		s.SourceType = t
	}
	if d, ok := m["data"].(string); ok {
		s.Data = d
	}
	if mt, ok := m["media_type"].(string); ok {
		s.MediaType = mt
	}
	return s
}

func decodeVideoSource(v any) *VideoSource {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	s := &VideoSource{}
	if t, ok := m["type"].(string); ok {
		s.SourceType = t
	}
	if d, ok := m["data"].(string); ok {
		s.Data = d
	}
	if mt, ok := m["media_type"].(string); ok {
		s.MediaType = mt
	}
	return s
}

func decodeAudioSource(v any) *AudioSource {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	s := &AudioSource{}
	if t, ok := m["type"].(string); ok {
		s.SourceType = t
	}
	if d, ok := m["data"].(string); ok {
		s.Data = d
	}
	if mt, ok := m["media_type"].(string); ok {
		s.MediaType = mt
	}
	return s
}

func decodeDocumentSource(v any) *DocumentSource {
	m, ok := v.(map[string]any)
	if !ok {
		return nil
	}
	s := &DocumentSource{}
	if t, ok := m["type"].(string); ok {
		s.SourceType = t
	}
	if d, ok := m["data"].(string); ok {
		s.Data = d
	}
	if mt, ok := m["media_type"].(string); ok {
		s.MediaType = mt
	}
	if title, ok := m["title"].(string); ok {
		s.Title = title
	}
	return s
}
