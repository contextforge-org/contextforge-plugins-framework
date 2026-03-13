"""A filter plugin.

Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: habeck

This module loads configurations for plugins.
"""

import logging

# First-Party
from cpex.framework import (
    Plugin,
    PluginConfig,
    PluginContext,
    PromptPosthookPayload,
    PromptPosthookResult,
    PromptPrehookPayload,
    PromptPrehookResult,
    ToolPostInvokePayload,
    ToolPostInvokeResult,
    ToolPreInvokePayload,
    ToolPreInvokeResult,
)
from cpex.framework.hooks.agents import (
    AgentPostInvokePayload,
    AgentPostInvokeResult,
    AgentPreInvokePayload,
    AgentPreInvokeResult,
)
from cpex.framework.hooks.resources import (
    ResourcePostFetchPayload,
    ResourcePostFetchResult,
    ResourcePreFetchPayload,
    ResourcePreFetchResult,
)

logger = logging.getLogger(__name__)


class TestPlugin(Plugin):
    """A filter plugin."""

    def __init__(self, config: PluginConfig):
        """Entry init block for plugin.

        Args:
          logger: logger that the skill can make use of
          config: the skill configuration
        """
        super().__init__(config)

    async def prompt_pre_fetch(self, payload: PromptPrehookPayload, context: PluginContext) -> PromptPrehookResult:
        """The plugin hook run before a prompt is retrieved and rendered.

        Args:
            payload: The prompt payload to be analyzed.
            context: contextual information about the hook call.

        Returns:
            The result of the plugin's analysis, including whether the prompt can proceed.
        """
        logger.info("TestPlugin: prompt_pre_fetch")
        return PromptPrehookResult(continue_processing=True)

    async def prompt_post_fetch(self, payload: PromptPosthookPayload, context: PluginContext) -> PromptPosthookResult:
        """Plugin hook run after a prompt is rendered.

        Args:
            payload: The prompt payload to be analyzed.
            context: Contextual information about the hook call.

        Returns:
            The result of the plugin's analysis, including whether the prompt can proceed.
        """
        logger.info("TestPlugin: prompt_post_fetch")
        return PromptPosthookResult(continue_processing=True)

    async def tool_pre_invoke(self, payload: ToolPreInvokePayload, context: PluginContext) -> ToolPreInvokeResult:
        """Plugin hook run before a tool is invoked.

        Args:
            payload: The tool payload to be analyzed.
            context: Contextual information about the hook call.

        Returns:
            The result of the plugin's analysis, including whether the tool can proceed.
        """
        logger.info("TestPlugin: tool_pre_invoke")
        return ToolPreInvokeResult(continue_processing=True)

    async def tool_post_invoke(self, payload: ToolPostInvokePayload, context: PluginContext) -> ToolPostInvokeResult:
        """Plugin hook run after a tool is invoked.

        Args:
            payload: The tool result payload to be analyzed.
            context: Contextual information about the hook call.

        Returns:
            The result of the plugin's analysis, including whether the tool result should proceed.
        """
        logger.info("TestPlugin: tool_post_invoke")
        return ToolPostInvokeResult(continue_processing=True)

    async def resource_pre_fetch(
        self, payload: ResourcePreFetchPayload, context: PluginContext
    ) -> ResourcePreFetchResult:
        """Plugin hook run before a resource is fetched.
        Args:
            payload: The resource payload to be analyzed.
            context: Contextual information about the hook call.
        """
        logger.info("TestPlugin: resource_pre_fetch")
        return ResourcePreFetchResult(continue_processing=True)

    async def resource_post_fetch(
        self, payload: ResourcePostFetchPayload, context: PluginContext
    ) -> ResourcePostFetchResult:
        """Plugin hook run after a resource is fetched.
        Args:
            payload: The resource payload to be analyzed.
            context: Contextual information about the hook call.
        """
        logger.info("TestPlugin: resource_post_fetch")
        return ResourcePostFetchResult(continue_processing=True)

    async def agent_pre_invoke(self, payload: AgentPreInvokePayload, context: PluginContext) -> AgentPreInvokeResult:
        """Plugin hook run before an agent is invoked.
        Args:
            payload: The agent payload to be analyzed.
            context: Contextual information about the hook call.
        """
        logger.info("TestPlugin: agent_pre_invoke")
        return AgentPreInvokeResult(continue_processing=True)

    async def agent_post_invoke(self, payload: AgentPostInvokePayload, context: PluginContext) -> AgentPostInvokeResult:
        """Plugin hook run after an agent is invoked.
        Args:
            payload: The agent payload to be analyzed.
            context: Contextual information about the hook call.
        """
        logger.info("TestPlugin: agent_post_invoke")
        return AgentPostInvokeResult(continue_processing=True)
