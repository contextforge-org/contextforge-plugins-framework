# -*- coding: utf-8 -*-
"""Location: ./cpex/framework/isolated/client.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Ted Habeck

Isolated plugin client
Module that contains plugin client code to serve venv isolated plugins.
"""

import logging
import os
from pathlib import Path
import sys
import venv

from typing_extensions import Any

from cpex.framework.base import Plugin
from cpex.framework.constants import CONTEXT, HOOK_TYPE, PAYLOAD, PLUGIN_NAME
from cpex.framework.errors import PluginError, convert_exception_to_error
from cpex.framework.hooks.prompts import PromptPosthookResult, PromptPrehookResult
from cpex.framework.hooks.registry import get_hook_registry
from cpex.framework.hooks.tools import ToolPostInvokeResult, ToolPreInvokeResult
from cpex.framework.isolated.venv_comm import VenvProcessCommunicator
from cpex.framework.models import PluginConfig, PluginContext, PluginErrorModel, PluginPayload, PluginResult

logger = logging.getLogger(__name__)


class IsolatedVenvPlugin(Plugin):
    """IsolatedVenvPlugin class."""

    def __init__(self, config: PluginConfig) -> None:
        """Initialize the plugin's venv environment."""
        super().__init__(config)
        self.implementation = "Python"
        self.comm = None
        self.script_path: str = config.config["script_path"]

    async def create_venv(self, venv_path: str = ".venv") -> None:
        """Create a new venv environment."""
        # Check Python version
        python_version = sys.version_info
        print(f"Current Python version: {python_version.major}.{python_version.minor}.{python_version.micro}")
        # Create the EnvBuilder with common options
        builder = venv.EnvBuilder(
            system_site_packages=True,  # Don't include system site-packages
            clear=False,  # Don't clear existing venv if it exists
            symlinks=False,  # Use symlinks (recommended on Unix-like systems)
            upgrade=False,  # Don't upgrade existing venv
            with_pip=True,  # Install pip in the venv
            prompt=None,  # Use default prompt (directory name)
        )
        # Create the virtual environment
        print(f"\nCreating virtual environment at: {os.path.abspath(venv_path)}")
        try:
            builder.create(venv_path)
            print("✓ Virtual environment created successfully!")
            print("\nTo activate the virtual environment:")
            print(f"  source {venv_path}/bin/activate  # On Unix/macOS")
            print(f"  {venv_path}\\Scripts\\activate  # On Windows")
        except Exception as e:
            print(f"✗ Error creating virtual environment: {e}")
            raise e

    # Called by plugins/framework/loader/plugin.py load_and_instantiate_plugin()
    # The plugins/framework/manager.py class (PluginManager) loads and registers the plugin
    async def initialize(self) -> None:
        """Initialize the plugin's venv environment."""
        # ensure the config is validated
        path = Path(self.config.config.get("script_path")).resolve()
        if not os.path.exists(path):
            raise FileNotFoundError(f"script_path not found: {path}")

        self.venv = await self.create_venv(self.config.config["venv_path"])
        self.comm = VenvProcessCommunicator(self.config.config["venv_path"])
        self.comm.install_requirements(self.config.config["requirements_file"])

    async def invoke_hook(self, hook_type: str, payload: PluginPayload, context: PluginContext) -> PluginResult:
        """Invoke a plugin in the context of the active venv (self.comm)"""
        registry = get_hook_registry()
        result_type = registry.get_result_type(hook_type)
        if not result_type:
            raise PluginError(
                error=PluginErrorModel(
                    message=f"Hook type '{hook_type}' not registered in hook registry", plugin_name=self.name
                )
            )

        if not self.comm:
            raise PluginError(error=PluginErrorModel(message="Plugin comm not initialized", plugin_name=self.name))

        safe_config = self.config.get_safe_config()

        try:
            # Serialize payload and context to ensure they are JSON-serializable
            serialized_payload = payload.model_dump(mode="json") if payload else None
            serialized_context = context.model_dump(mode="json") if context else None

            # build up the task to send
            task = {
                "task_type": "load_and_run_hook",
                "script_path": self.config.config["script_path"],
                "class_name": self.config.config["class_name"],
                "config": safe_config,
                HOOK_TYPE: hook_type,
                PLUGIN_NAME: self.name,
                PAYLOAD: serialized_payload,
                CONTEXT: serialized_context,
            }
            result: Any = self.comm.send_task(script_path="cpex/framework/isolated/worker.py", task_data=task)
            #
            # This is going to be tricky.  Need to see what the response is and initialize the proper result object from the dict
            # task_data
            if hook_type == "tool_pre_invoke":
                result = ToolPreInvokeResult(
                    continue_processing=result.get("continue_processing"),
                    modified_payload=result.get("modified_payload"),
                    violation=result.get("violation"),
                    metadata=result.get("metadata"),
                )
            if hook_type == "tool_post_invoke":
                result = ToolPostInvokeResult(
                    continue_processing=result.get("continue_processing"),
                    modified_payload=result.get("modified_payload"),
                    violation=result.get("violation"),
                    metadata=result.get("metadata"),
                )
            if hook_type == "prompt_pre_fetch":
                result = PromptPrehookResult(
                    continue_processing=result.get("continue_processing"),
                    modified_payload=result.get("modified_payload"),
                    violation=result.get("violation"),
                    metadata=result.get("metadata"),
                )
            if hook_type == "prompt_post_fetch":
                result = PromptPosthookResult(
                    continue_processing=result.get("continue_processing"),
                    modified_payload=result.get("modified_payload"),
                    violation=result.get("violation"),
                    metadata=result.get("metadata"),
                )
            return result
        except PluginError as pe:
            logger.exception(pe)
            raise
        except Exception as e:
            logger.exception(e)
            raise PluginError(error=convert_exception_to_error(e, plugin_name=self.name)) from e
