# -*- coding: utf-8 -*-
"""Location: ./cpex/framework/isolated/worker.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Ted Habeck, Fred Araujo

Isolated plugin server
Module that contains plugin server code to invoke hooks in native plugins.
"""

import asyncio
import importlib.metadata
import json
import logging
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Type, cast

from cpex.framework.base import HookRef, Plugin, PluginRef
from cpex.framework.constants import HOOK_TYPE
from cpex.framework.loader.config import ConfigLoader
from cpex.framework.manager import PluginExecutor
from cpex.framework.models import PluginContext
from cpex.framework.utils import parse_class_name

logger = logging.getLogger(__name__)


def get_environment_info():
    """Get information about current Python environment."""
    return {
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "installed_packages": [str(d) for d in importlib.metadata.entry_points()][:10],  # First 10 packages
    }


def get_proper_config(name, module_path):
    """
    Load a config which has all it's proper decorations
    """
    plugin_loader_config = ConfigLoader.load_config(Path(f"{module_path}/config.yaml").resolve(), use_jinja=False)
    plugins: list[dict] = []
    config = None
    if plugin_loader_config.plugins:
        for plug in plugin_loader_config.plugins:
            plugins.append(plug.model_dump())
            if plug.name == name:
                # config = plug.model_dump()
                config = plug
                return config
    return None


async def process_task(task_data):
    """Process the task received from parent."""
    task_type = task_data.get("task_type")

    if task_type == "info":
        return {
            "status": "success",
            "environment": get_environment_info(),
            "message": "Environment info retrieved successfully",
        }
    # This is essentially emulating the plugin loader's load and instantiate plugin
    if task_type == "load_and_run_hook":
        # relative path from project root.
        json_config = task_data.get("config")
        config_raw = json.loads(json_config)
        module_path: str = task_data.get("script_path")
        sys.path.append(str(Path(module_path).resolve()))
        config = get_proper_config(config_raw.get("name"), module_path)
        hook_type = task_data.get(HOOK_TYPE)
        cls_name: str = task_data.get("class_name")
        mod_name, n_cls_name = parse_class_name(cls_name)
        module: ModuleType = importlib.import_module(mod_name)
        # cool, we found the module, and verified it implemented the hook type.
        class_ = getattr(module, n_cls_name)
        plugin_type = cast(Type[Plugin], class_)
        plugin = plugin_type(config)
        await plugin.initialize()
        # now invoke the hook
        plugin_ref = PluginRef(plugin)
        hook_ref = HookRef(hook_type, plugin_ref)
        executor = PluginExecutor(None, 30)
        # retrieve the context
        context = task_data.get("context")
        plugin_context = PluginContext(
            state=context.get("state"), global_context=context.get("global_context"), metadata=context.get("metadata")
        )
        result = await executor.execute_plugin(
            hook_ref, payload=task_data.get("payload"), local_context=plugin_context, violations_as_exceptions=False
        )
        return result


async def main():
    """Main function - continuously read from stdin, process tasks, write to stdout."""
    logger.info("Worker process started, waiting for tasks...")

    try:
        # Continuously read and process tasks
        while True:
            try:
                # Read one line at a time
                line = sys.stdin.readline()

                # Check for EOF
                if not line:
                    logger.info("EOF received, shutting down worker")
                    break

                # Parse the task
                task_data = json.loads(line.strip())
                request_id = task_data.get("request_id", "unknown")

                # Check for shutdown signal
                if task_data.get("task_type") == "shutdown":
                    logger.info("Shutdown signal received")
                    response = {"status": "success", "message": "Shutting down", "request_id": request_id}
                    print(json.dumps(response), flush=True)
                    break

                # Process the task
                response = await process_task(task_data)

                # Serialize response
                if response:
                    serializable_response = response.model_dump(mode="json")
                else:
                    serializable_response = {"status": "success"}

                # Add request_id to response
                serializable_response["request_id"] = request_id

                # Send response back to parent (one line per response)
                print(json.dumps(serializable_response), flush=True)

            except json.JSONDecodeError as e:
                error_response = {
                    "status": "error",
                    "message": f"Invalid JSON input: {str(e)}",
                    "request_id": task_data.get("request_id", "unknown") if "task_data" in locals() else "unknown",
                }
                print(json.dumps(error_response), flush=True)

            except Exception as e:
                logger.error("Error processing task: %s", str(e))
                error_response = {
                    "status": "error",
                    "message": f"Unexpected error: {str(e)}",
                    "request_id": task_data.get("request_id", "unknown") if "task_data" in locals() else "unknown",
                }
                print(json.dumps(error_response), flush=True)

    except KeyboardInterrupt:
        logger.info("Worker interrupted")
    except Exception:
        logger.exception("Fatal error in worker main loop")
    finally:
        logger.info("Worker process shutting down")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
