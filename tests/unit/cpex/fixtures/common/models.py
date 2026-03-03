# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/fixtures/common/models.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0
Authors: Fred Araujo

MCP Protocol Type Definitions for tests.
"""

# Standard
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

# Third-Party
from pydantic import BaseModel, Field


class Role(str, Enum):
    """Message role in conversations."""

    ASSISTANT = "assistant"
    USER = "user"


# Base content types
class TextContent(BaseModel):
    """Text content for messages (MCP spec-compliant)."""

    type: Literal["text"]
    text: str
    annotations: Optional[Any] = None
    meta: Optional[Dict[str, Any]] = Field(None, alias="_meta")


class ResourceContents(BaseModel):
    """Base class for resource contents (MCP spec-compliant)."""

    uri: str
    mime_type: Optional[str] = Field(None, alias="mimeType")
    meta: Optional[Dict[str, Any]] = Field(None, alias="_meta")


# Legacy ResourceContent for backwards compatibility
class ResourceContent(BaseModel):
    """Resource content that can be embedded (LEGACY - use TextResourceContents or BlobResourceContents)."""

    type: Literal["resource"]
    id: str
    uri: str
    mime_type: Optional[str] = None
    text: Optional[str] = None
    blob: Optional[bytes] = None


ContentType = Union[TextContent, ResourceContent]


# Message types
class Message(BaseModel):
    """A message in a conversation.

    Attributes:
        role (Role): The role of the message sender.
        content (ContentType): The content of the message.
    """

    role: Role
    content: ContentType


class PromptMessage(BaseModel):
    """Message in a prompt (MCP spec-compliant)."""

    role: Role
    content: "ContentBlock"  # Uses ContentBlock union (includes ResourceLink and EmbeddedResource)


class PromptResult(BaseModel):
    """Result of rendering a prompt template.

    Attributes:
        messages (List[Message]): The list of messages produced by rendering the prompt.
        description (Optional[str]): An optional description of the rendered result.
    """

    messages: List[Message]
    description: Optional[str] = None


# MCP spec-compliant ContentBlock union for prompts and tool results
# Per spec: ContentBlock can include ResourceLink and EmbeddedResource
ContentBlock = Union[TextContent]
