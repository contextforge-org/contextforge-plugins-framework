// Location: ./crates/cpex-core/src/cmf/mod.rs
// Copyright 2025
// SPDX-License-Identifier: Apache-2.0
// Authors: Teryl Taylor
//
// ContextForge Message Format (CMF).
//
// Canonical message representation for interactions between users,
// agents, tools, and language models. All models mirror the Python
// CMF in cpex/framework/cmf/message.py.
//
// Extensions are NOT part of the Message — they are passed separately
// to handlers via the framework's Extensions type in hooks/payload.rs.
// This allows extensions to be shared across payload types and avoids
// copying the message when extensions change.

pub mod content;
pub mod enums;
pub mod message;
pub mod view;

// Re-export key types at the cmf module level
pub use content::*;
pub use enums::*;
pub use message::{CmfHook, Message, MessagePayload};
pub use view::{MessageView, ViewAction, ViewKind};
