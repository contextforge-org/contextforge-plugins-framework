# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/framework/isolated/test_client.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Ted Habeck

Unit tests for IsolatedVenvPlugin.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cpex.framework.errors import PluginError
from cpex.framework.hooks.prompts import PromptPosthookResult, PromptPrehookResult
from cpex.framework.hooks.tools import ToolPostInvokeResult, ToolPreInvokeResult
from cpex.framework.isolated.client import IsolatedVenvPlugin
from cpex.framework.models import PluginConfig, PluginContext, PluginErrorModel


class TestIsolatedVenvPlugin:
    """Test suite for IsolatedVenvPlugin class."""

    @pytest.fixture
    def mock_config(self, tmp_path):
        """Create a mock plugin configuration."""
        venv_path = tmp_path / ".venv"
        script_path = "tests/unit/cpex/fixtures/plugins/isolated/test_plugin/requirements.txt"
        requirements_file = tmp_path / "requirements.txt"

        # config_dict = {
        #     "name": "test_isolated_plugin",
        #     "kind": "isolated_venv",
        #     "description": "Test isolated plugin",
        #     "version": "1.0.0",
        #     "author": "Test Author",
        #     "hooks": ["tool_pre_invoke", "tool_post_invoke"],
        #     "config": {
        #         "venv_path": str(venv_path),
        #         "script_path": str(script_path),
        #         "requirements_file": str(requirements_file),
        #         "class_name": "test_plugin.TestPlugin",
        #     },
        # }
        config_dict = {
            "name": "test_plugin",
            "kind": "isolated_venv",
            "description": "Test plugin",
            "version": "1.0.0",
            "author": "Test",
            "hooks": ["tool_pre_invoke"],
            "config": {
                "class_name": "test_plugin.TestPlugin",
                "venv_path": venv_path,
                "requirements_file": requirements_file,
                "script_path": script_path
            }
        }

        return PluginConfig(**config_dict)

    @pytest.fixture
    def plugin(self, mock_config):
        """Create an IsolatedVenvPlugin instance."""
        return IsolatedVenvPlugin(mock_config)

    @pytest.fixture
    def plugin_context(self):
        """Create a PluginContext instance"""
        context = {"state": {}, "global_context": {"request_id": "req-123"}, "metadata": {}}
        plugin_context = PluginContext(
            state=context.get("state"), global_context=context.get("global_context"), metadata=context.get("metadata")
        )
        return plugin_context

    def test_init(self, plugin, mock_config):
        """Test plugin initialization."""
        assert plugin.name == "test_plugin"
        assert plugin.implementation == "Python"
        assert plugin.script_path == mock_config.config["script_path"]
        assert plugin.comm is None

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.venv.EnvBuilder")
    async def test_create_venv_success(self, mock_builder_class, plugin, tmp_path):
        """Test successful venv creation."""
        venv_path = tmp_path / ".venv"
        mock_builder = MagicMock()
        mock_builder_class.return_value = mock_builder

        await plugin.create_venv(str(venv_path))

        mock_builder_class.assert_called_once()
        mock_builder.create.assert_called_once_with(str(venv_path))

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.venv.EnvBuilder")
    async def test_create_venv_failure(self, mock_builder_class, plugin, tmp_path):
        """Test venv creation failure."""
        venv_path = tmp_path / ".venv"
        mock_builder = MagicMock()
        mock_builder.create.side_effect = Exception("Creation failed")
        mock_builder_class.return_value = mock_builder

        with pytest.raises(Exception, match="Creation failed"):
            await plugin.create_venv(str(venv_path))

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.VenvProcessCommunicator")
    @patch.object(IsolatedVenvPlugin, "create_venv")
    async def test_initialize_success(self, mock_create_venv, mock_comm_class, plugin):
        """Test successful plugin initialization."""
        mock_create_venv.return_value = None
        mock_comm = MagicMock()
        mock_comm_class.return_value = mock_comm

        await plugin.initialize()

        mock_create_venv.assert_called_once()
        mock_comm_class.assert_called_once()
        mock_comm.install_requirements.assert_called_once()
        assert plugin.comm is not None

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_unregistered_hook_type(self, mock_get_registry, plugin, plugin_context):
        """Test invoking an unregistered hook type."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = None
        mock_get_registry.return_value = mock_registry

        plugin.comm = MagicMock()

        with pytest.raises(PluginError, match="Hook type .* not registered"):
            await plugin.invoke_hook("invalid_hook", None, plugin_context)

    @pytest.mark.asyncio
    async def test_invoke_hook_no_comm(self, plugin, plugin_context):
        """Test invoking hook without initialized communicator."""
        plugin.comm = None
        with pytest.raises(PluginError, match="Plugin comm not initialized"):
            await plugin.invoke_hook("tool_pre_invoke", None, plugin_context)

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_tool_pre_invoke_success(self, mock_get_registry, plugin, plugin_context):
        """Test successful tool_pre_invoke hook invocation."""
        # Setup registry
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPreInvokeResult
        mock_get_registry.return_value = mock_registry

        # Setup communicator
        mock_comm = MagicMock()
        response_data = {
            "continue_processing": True,
            "modified_payload": {"name": "test_tool", "args": {}},
            "violation": None,
            "metadata": {},
        }
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        # Create payload and context
        from cpex.framework.hooks.tools import ToolPreInvokePayload

        payload = ToolPreInvokePayload(name="test_tool", args={})
        result = await plugin.invoke_hook("tool_pre_invoke", payload, plugin_context)

        assert isinstance(result, ToolPreInvokeResult)
        assert result.continue_processing is True
        mock_comm.send_task.assert_called_once()

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_tool_post_invoke_success(self, mock_get_registry, plugin, plugin_context):
        """Test successful tool_post_invoke hook invocation."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPostInvokeResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        response_data = {
            "continue_processing": True,
            "modified_payload": {"name": "test_tool", "result": "success"},
            "violation": None,
            "metadata": {},
        }
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        from cpex.framework.hooks.tools import ToolPostInvokePayload

        payload = ToolPostInvokePayload(name="test_tool", result="success")

        result = await plugin.invoke_hook("tool_post_invoke", payload, plugin_context)

        assert isinstance(result, ToolPostInvokeResult)
        assert result.continue_processing is True

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_prompt_pre_fetch_success(self, mock_get_registry, plugin, plugin_context):
        """Test successful prompt_pre_fetch hook invocation."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = PromptPrehookResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        response_data = {
            "continue_processing": True,
            "modified_payload": {"prompt_id": "test", "args": {}},
            "violation": None,
            "metadata": {},
        }
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        from cpex.framework.hooks.prompts import PromptPrehookPayload

        payload = PromptPrehookPayload(prompt_id="test", args={})

        result = await plugin.invoke_hook("prompt_pre_fetch", payload, plugin_context)

        assert isinstance(result, PromptPrehookResult)
        assert result.continue_processing is True

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_prompt_post_fetch_success(self, mock_get_registry, plugin, plugin_context):
        """Test successful prompt_post_fetch hook invocation."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = PromptPosthookResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        response_data = {
            "continue_processing": True,
            "modified_payload": {"prompt_id": "test", "result": {}},
            "violation": None,
            "metadata": {},
        }
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        from cpex.framework.hooks.prompts import PromptPosthookPayload

        payload = PromptPosthookPayload(prompt_id="test", result={})
        result = await plugin.invoke_hook("prompt_post_fetch", payload, plugin_context)

        assert isinstance(result, PromptPosthookResult)
        assert result.continue_processing is True

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_with_violation(self, mock_get_registry, plugin, plugin_context):
        """Test hook invocation that returns a violation."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPreInvokeResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        response_data = {
            "continue_processing": False,
            "modified_payload": None,
            "violation": {"reason": "Policy violation", "description":"severity high", "code": "PROHIBITED_CONTENT"},
            "metadata": {},
        }
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        from cpex.framework.hooks.tools import ToolPreInvokePayload

        payload = ToolPreInvokePayload(name="test_tool", args={})

        result = await plugin.invoke_hook("tool_pre_invoke", payload, plugin_context)

        assert isinstance(result, ToolPreInvokeResult)
        assert result.continue_processing is False
        assert result.violation is not None

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_plugin_error(self, mock_get_registry, plugin, plugin_context):
        """Test hook invocation that raises PluginError."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPreInvokeResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        mock_comm.send_task.side_effect = PluginError(
            error=PluginErrorModel(message="Test error", plugin_name="test_plugin")
        )
        plugin.comm = mock_comm

        from cpex.framework.hooks.tools import ToolPreInvokePayload

        payload = ToolPreInvokePayload(name="test_tool", args={})
        with pytest.raises(PluginError):
            await plugin.invoke_hook("tool_pre_invoke", payload, plugin_context)

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    @patch("cpex.framework.isolated.client.convert_exception_to_error")
    async def test_invoke_hook_generic_exception(self, mock_convert, mock_get_registry, plugin, plugin_context):
        """Test hook invocation that raises generic exception."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPreInvokeResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        mock_comm.send_task.side_effect = ValueError("Test error")
        plugin.comm = mock_comm

        mock_convert.return_value = PluginErrorModel(message="Converted error", plugin_name="test_plugin")

        from cpex.framework.hooks.tools import ToolPreInvokePayload

        payload = ToolPreInvokePayload(name="test_tool", args={})

        with pytest.raises(PluginError):
            await plugin.invoke_hook("tool_pre_invoke", payload, plugin_context)

        mock_convert.assert_called_once()

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.client.get_hook_registry")
    async def test_invoke_hook_serialization(self, mock_get_registry, plugin):
        """Test that payload and context are properly serialized."""
        mock_registry = MagicMock()
        mock_registry.get_result_type.return_value = ToolPreInvokeResult
        mock_get_registry.return_value = mock_registry

        mock_comm = MagicMock()
        response_data = {"continue_processing": True, "modified_payload": None, "violation": None, "metadata": {}}
        mock_comm.send_task.return_value = response_data
        plugin.comm = mock_comm

        from cpex.framework.hooks.tools import ToolPreInvokePayload
        from cpex.framework import GlobalContext

        payload = ToolPreInvokePayload(name="test_tool", args={"key": "value"})
        global_ctx = GlobalContext(request_id="req-123", user="alice")
        context = PluginContext(global_context=global_ctx)

        await plugin.invoke_hook("tool_pre_invoke", payload, context)

        # Verify send_task was called with serialized data
        call_args = mock_comm.send_task.call_args
        task_data = call_args[1]["task_data"]

        assert "payload" in task_data
        assert "context" in task_data
        assert task_data["hook_type"] == "tool_pre_invoke"
        assert task_data["plugin_name"] == plugin.name

    def test_get_safe_config(self, plugin):
        """Test that get_safe_config returns sanitized config."""
        safe_config = plugin.config.get_safe_config()
        assert isinstance(safe_config, str)
        # Should be valid JSON
        import json

        config_dict = json.loads(safe_config)
        assert "name" in config_dict


# Made with Bob
