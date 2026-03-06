# -*- coding: utf-8 -*-
"""Location: ./cpex/framework/hooks/message.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Teryl Taylor

Hook definitions for CMF Message evaluation.

Provides a unified entry point for policy evaluation on messages
flowing through the system. Plugins receive a MessagePayload
wrapping the CMF Message and can use Message.iter_views() for
granular per-content-part inspection.
"""

# Standard
from enum import Enum

# Third-Party
from pydantic import Field

# First-Party
from cpex.framework.cmf.message import Message
from cpex.framework.models import PluginPayload, PluginResult


class MessageHookType(str, Enum):
    """Message hook points.

    The hook type indicates *where* in the pipeline the evaluation
    is happening, enabling plugins to register for specific locations.

    Attributes:
        EVALUATE: Generic message evaluation.
        LLM_INPUT: Before model/LLM call (user messages going to LLM).
        LLM_OUTPUT: After model/LLM call (LLM response).
        TOOL_PRE_INVOKE: Before tool execution (tool call arguments).
        TOOL_POST_INVOKE: After tool execution (tool result).
        PROMPT_PRE_FETCH: Before prompt template fetch.
        PROMPT_POST_FETCH: After prompt template fetch.
        RESOURCE_PRE_FETCH: Before resource fetch.
        RESOURCE_POST_FETCH: After resource fetch.

    Examples:
        >>> MessageHookType.EVALUATE
        <MessageHookType.EVALUATE: 'evaluate'>
        >>> MessageHookType.LLM_INPUT
        <MessageHookType.LLM_INPUT: 'llm_input'>
    """

    EVALUATE = "evaluate"
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    TOOL_PRE_INVOKE = "tool_pre_invoke"
    TOOL_POST_INVOKE = "tool_post_invoke"
    PROMPT_PRE_FETCH = "prompt_pre_fetch"
    PROMPT_POST_FETCH = "prompt_post_fetch"
    RESOURCE_PRE_FETCH = "resource_pre_fetch"
    RESOURCE_POST_FETCH = "resource_post_fetch"


class MessagePayload(PluginPayload):
    """Payload for message evaluation hooks.

    Wraps a CMF Message for processing through the plugin pipeline.
    Plugins access the message and use iter_views() for per-content-part
    policy evaluation.

    Attributes:
        message: The CMF message to evaluate.
        hook: The hook location where this evaluation is happening.

    Examples:
        >>> from cpex.framework.cmf.message import Message, Role, TextContent
        >>> msg = Message(
        ...     role=Role.USER,
        ...     content=[TextContent(text="Hello")],
        ... )
        >>> payload = MessagePayload(
        ...     message=msg, hook=MessageHookType.LLM_INPUT
        ... )
        >>> payload.hook
        <MessageHookType.LLM_INPUT: 'llm_input'>
    """

    message: Message = Field(description="The CMF message to evaluate.")
    hook: MessageHookType = Field(
        default=MessageHookType.EVALUATE,
        description="The hook location where this evaluation is happening.",
    )


MessageResult = PluginResult[MessagePayload]
"""Result type for message evaluation hooks."""


def _register_message_hooks() -> None:
    """Register message hooks in the global registry.

    Called at module load time. Idempotent — skips registration
    if the hook is already registered.
    """
    # First-Party
    from cpex.framework.hooks.registry import get_hook_registry  # pylint: disable=import-outside-toplevel

    registry = get_hook_registry()

    if not registry.is_registered(MessageHookType.EVALUATE):
        registry.register_hook(MessageHookType.EVALUATE, MessagePayload, MessageResult)


_register_message_hooks()
