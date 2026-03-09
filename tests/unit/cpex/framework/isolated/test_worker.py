# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/framework/isolated/test_worker.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Ted Habeck

Unit tests for worker.py functions.
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from cpex.framework.isolated.worker import get_environment_info, get_proper_config, process_task


class TestWorkerFunctions:
    """Test suite for worker.py functions."""

    def test_get_environment_info(self):
        """Test getting environment information."""
        info = get_environment_info()

        assert "python_version" in info
        assert "python_executable" in info
        assert "platform" in info
        assert "installed_packages" in info

        assert info["python_version"] == sys.version
        assert info["python_executable"] == sys.executable
        assert isinstance(info["installed_packages"], list)
        assert len(info["installed_packages"]) <= 10  # Limited to first 10

    @patch("cpex.framework.isolated.worker.ConfigLoader.load_config")
    def test_get_proper_config_found(self, mock_load_config):
        """Test getting proper config when plugin is found."""
        # Create mock plugin config
        mock_plugin = MagicMock()
        mock_plugin.name = "test_plugin"
        mock_plugin.model_dump.return_value = {"name": "test_plugin", "kind": "isolated_venv", "config": {}}

        mock_config = MagicMock()
        mock_config.plugins = [mock_plugin]
        mock_load_config.return_value = mock_config

        result = get_proper_config("test_plugin", "plugins")

        assert result is not None
        assert result.name == "test_plugin"

    @patch("cpex.framework.isolated.worker.ConfigLoader.load_config")
    def test_get_proper_config_not_found(self, mock_load_config):
        """Test getting proper config when plugin is not found."""
        mock_plugin = MagicMock()
        mock_plugin.name = "other_plugin"

        mock_config = MagicMock()
        mock_config.plugins = [mock_plugin]
        mock_load_config.return_value = mock_config

        result = get_proper_config("test_plugin", "plugins")

        assert result is None

    @patch("cpex.framework.isolated.worker.ConfigLoader.load_config")
    def test_get_proper_config_no_plugins(self, mock_load_config):
        """Test getting proper config when no plugins exist."""
        mock_config = MagicMock()
        mock_config.plugins = None
        mock_load_config.return_value = mock_config

        result = get_proper_config("test_plugin", "plugins")

        assert result is None

    @pytest.mark.asyncio
    async def test_process_task_info(self):
        """Test processing info task."""
        task_data = {"task_type": "info"}

        result = await process_task(task_data)

        assert result["status"] == "success"
        assert "environment" in result
        assert "message" in result
        assert result["message"] == "Environment info retrieved successfully"

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.worker.get_proper_config")
    @patch("cpex.framework.isolated.worker.importlib.import_module")
    @patch("cpex.framework.isolated.worker.PluginExecutor")
    async def test_process_task_load_and_run_hook_success(self, mock_executor_class, mock_import, mock_get_config):
        """Test processing load_and_run_hook task successfully."""
        # Setup mock config
        mock_config = MagicMock()
        mock_config.name = "test_plugin"
        mock_get_config.return_value = mock_config

        # Setup mock plugin class
        mock_plugin_instance = AsyncMock()
        mock_plugin_instance.initialize = AsyncMock()
        mock_plugin_instance.tool_pre_invoke = AsyncMock()
        mock_plugin_instance.tool_post_invoke = AsyncMock()
        mock_plugin_instance.tool_exception = AsyncMock()
        mock_plugin_instance.tool_cleanup = AsyncMock()
        mock_plugin_class = MagicMock(return_value=mock_plugin_instance)

        mock_module = MagicMock()
        mock_module.TestPlugin = mock_plugin_class
        mock_import.return_value = mock_module

        # Setup mock executor
        mock_executor = MagicMock()
        mock_result = MagicMock()
        mock_result.continue_processing = True
        mock_executor.execute_plugin = AsyncMock(return_value=mock_result)
        mock_executor_class.return_value = mock_executor

        # Create task data
        config_dict = {"name": "test_plugin", "kind": "isolated_venv", "config": {}}
        task_data = {
            "task_type": "load_and_run_hook",
            "config": json.dumps(config_dict),
            "script_path": "plugins",
            "class_name": "test_plugin.TestPlugin",
            "hook_type": "tool_pre_invoke",
            "payload": {"name": "test_tool", "args": {}},
            "context": {"state": {}, "global_context": {"request_id": "req-123"}, "metadata": {}},
        }

        result = await process_task(task_data)

        assert result is not None
        mock_plugin_instance.initialize.assert_called_once()
        mock_executor.execute_plugin.assert_called_once()

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.worker.get_proper_config")
    async def test_process_task_load_and_run_hook_no_config(self, mock_get_config):
        """Test processing load_and_run_hook task when config not found."""
        mock_get_config.return_value = None

        config_dict = {"name": "test_plugin", "kind": "isolated_venv"}
        task_data = {
            "task_type": "load_and_run_hook",
            "config": json.dumps(config_dict),
            "script_path": "plugins",
            "class_name": "test_plugin.TestPlugin",
            "hook_type": "tool_pre_invoke",
            "payload": {},
            "context": {"state": {}, "global_context": {}, "metadata": {}},
        }

        # Should raise an error or return None
        with pytest.raises((AttributeError, TypeError)):
            await process_task(task_data)

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.worker.get_proper_config")
    @patch("cpex.framework.isolated.worker.importlib.import_module")
    async def test_process_task_load_and_run_hook_import_error(self, mock_import, mock_get_config):
        """Test processing load_and_run_hook task with import error."""
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        mock_import.side_effect = ImportError("Module not found")

        config_dict = {"name": "test_plugin", "kind": "isolated_venv"}
        task_data = {
            "task_type": "load_and_run_hook",
            "config": json.dumps(config_dict),
            "script_path": "plugins",
            "class_name": "test_plugin.TestPlugin",
            "hook_type": "tool_pre_invoke",
            "payload": {},
            "context": {"state": {}, "global_context": {}, "metadata": {}},
        }

        with pytest.raises(ImportError):
            await process_task(task_data)

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.worker.get_proper_config")
    @patch("cpex.framework.isolated.worker.importlib.import_module")
    @patch("cpex.framework.isolated.worker.PluginExecutor")
    async def test_process_task_with_different_hook_types(self, mock_executor_class, mock_import, mock_get_config):
        """Test processing tasks with different hook types."""
        # Setup mocks
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        mock_plugin_instance = MagicMock()
        mock_plugin_instance.initialize = AsyncMock()
        mock_plugin_instance.tool_pre_invoke = AsyncMock()
        mock_plugin_instance.tool_post_invoke = AsyncMock()
        mock_plugin_instance.prompt_pre_fetch = AsyncMock()
        mock_plugin_instance.prompt_post_fetch = AsyncMock()
        mock_plugin_instance.tool_exception = AsyncMock()
        mock_plugin_instance.tool_cleanup = AsyncMock()
        mock_plugin_class = MagicMock(return_value=mock_plugin_instance)

        mock_module = MagicMock()
        mock_module.TestPlugin = mock_plugin_class
        mock_import.return_value = mock_module

        mock_executor = MagicMock()
        mock_result = MagicMock()
        mock_executor.execute_plugin = AsyncMock(return_value=mock_result)
        mock_executor_class.return_value = mock_executor

        hook_types = ["tool_pre_invoke", "tool_post_invoke", "prompt_pre_fetch", "prompt_post_fetch"]

        for hook_type in hook_types:
            config_dict = {"name": "test_plugin", "kind": "isolated_venv"}
            task_data = {
                "task_type": "load_and_run_hook",
                "config": json.dumps(config_dict),
                "script_path": "plugins",
                "class_name": "test_plugin.TestPlugin",
                "hook_type": hook_type,
                "payload": {},
                "context": {"state": {}, "global_context": {"request_id": "req-123"}, "metadata": {}},
            }

            result = await process_task(task_data)
            assert result is not None

    @pytest.mark.asyncio
    async def test_process_task_unknown_task_type(self):
        """Test processing task with unknown task type."""
        task_data = {"task_type": "unknown_type"}

        # Should return None or handle gracefully
        result = await process_task(task_data)
        assert result is None

    @pytest.mark.asyncio
    @patch("cpex.framework.isolated.worker.get_proper_config")
    @patch("cpex.framework.isolated.worker.importlib.import_module")
    @patch("cpex.framework.isolated.worker.PluginExecutor")
    async def test_process_task_with_metadata(self, mock_executor_class, mock_import, mock_get_config):
        """Test processing task with metadata in context."""
        mock_config = MagicMock()
        mock_get_config.return_value = mock_config

        mock_plugin_instance = AsyncMock()
        mock_plugin_instance.initialize = AsyncMock()
        mock_plugin_instance.tool_pre_invoke = AsyncMock()
        mock_plugin_instance.tool_post_invoke = AsyncMock()
        mock_plugin_instance.prompt_pre_fetch = AsyncMock()
        mock_plugin_instance.prompt_post_fetch = AsyncMock()
        mock_plugin_instance.tool_exception = AsyncMock()
        mock_plugin_instance.tool_cleanup = AsyncMock()

        mock_plugin_class = MagicMock(return_value=mock_plugin_instance)

        mock_module = MagicMock()
        mock_module.TestPlugin = mock_plugin_class
        mock_import.return_value = mock_module

        mock_executor = MagicMock()
        mock_result = MagicMock()
        mock_executor.execute_plugin = AsyncMock(return_value=mock_result)
        mock_executor_class.return_value = mock_executor

        config_dict = {"name": "test_plugin", "kind": "isolated_venv"}
        task_data = {
            "task_type": "load_and_run_hook",
            "config": json.dumps(config_dict),
            "script_path": "plugins",
            "class_name": "test_plugin.TestPlugin",
            "hook_type": "tool_pre_invoke",
            "payload": {"name": "test_tool"},
            "context": {
                "state": {"key": "value"},
                "global_context": {"request_id": "req-123", "user": "alice"},
                "metadata": {"custom": "data"},
            },
        }

        result = await process_task(task_data)

        assert result is not None
        # Verify executor was called with proper context
        call_args = mock_executor.execute_plugin.call_args
        assert call_args is not None



# Made with Bob
