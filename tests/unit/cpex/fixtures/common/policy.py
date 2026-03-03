# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/fixtures/common/policy.py
Copyright 2026
SPDX-License-Identifier: Apache-2.0
Authors: Fred Araujo

Concrete hook payload policies for testing.
"""

# First-Party
from cpex.framework.hooks.policies import HookPayloadPolicy

HOOK_PAYLOAD_POLICIES: dict[str, HookPayloadPolicy] = {
    # Tools
    "tool_pre_invoke": HookPayloadPolicy(writable_fields=frozenset({"name", "args", "headers"})),
    "tool_post_invoke": HookPayloadPolicy(writable_fields=frozenset({"result"})),
    # Prompts
    "prompt_pre_fetch": HookPayloadPolicy(writable_fields=frozenset({"args"})),
    "prompt_post_fetch": HookPayloadPolicy(writable_fields=frozenset({"result"})),
    # Resources
    "resource_pre_fetch": HookPayloadPolicy(writable_fields=frozenset({"uri", "metadata"})),
    "resource_post_fetch": HookPayloadPolicy(writable_fields=frozenset({"content"})),
    # Agents
    "agent_pre_invoke": HookPayloadPolicy(
        writable_fields=frozenset({"agent_id", "messages", "tools", "model", "system_prompt", "parameters", "headers"})
    ),
    "agent_post_invoke": HookPayloadPolicy(writable_fields=frozenset({"messages", "tool_calls"})),
    # HTTP hooks (cross-type results — input and output payload types differ,
    # so field-level filtering is not applicable; policy presence authorises
    # the hook so it is never subject to default_hook_policy=deny).
    "http_pre_request": HookPayloadPolicy(writable_fields=frozenset({"headers"})),
    "http_post_request": HookPayloadPolicy(writable_fields=frozenset({"headers"})),
    "http_auth_resolve_user": HookPayloadPolicy(writable_fields=frozenset()),
    "http_auth_check_permission": HookPayloadPolicy(writable_fields=frozenset({"reason"})),
}
