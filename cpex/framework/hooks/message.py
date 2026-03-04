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

    Attributes:
        EVALUATE: Evaluate a message for policy decisions.

    Examples:
        >>> MessageHookType.EVALUATE
        <MessageHookType.EVALUATE: 'evaluate'>
        >>> MessageHookType.EVALUATE.value
        'evaluate'
    """

    EVALUATE = "evaluate"


class MessagePayload(PluginPayload):
    """Payload for message evaluation hooks.

    Wraps a CMF Message for processing through the plugin pipeline.
    Plugins access the message and use iter_views() for per-content-part
    policy evaluation.

    Attributes:
        message: The CMF message to evaluate.

    Examples:
        >>> from cpex.framework.cmf.message import Message, Role, TextContent
        >>> msg = Message(
        ...     role=Role.USER,
        ...     content=[TextContent(text="Hello")],
        ... )
        >>> payload = MessagePayload(message=msg)
        >>> payload.message.role
        <Role.USER: 'user'>
        >>> payload.message.content[0].text
        'Hello'

        >>> # Iterate views through the payload
        >>> views = list(payload.message.iter_views())
        >>> len(views)
        1
    """

    message: Message = Field(description="The CMF message to evaluate.")


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
