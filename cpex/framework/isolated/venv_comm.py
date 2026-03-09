# -*- coding: utf-8 -*-
"""
Location: ./cpex/framework/isolated/venv_comm.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Fred Araujo, Ted Habeck
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import orjson

logger = logging.getLogger(__name__)


class VenvProcessCommunicator:
    """Handles communication with child processes in different virtual environments."""

    def __init__(self, venv_path: str) -> None:
        """
        Initialize communicator with target virtual environment.

        Args:
            venv_path (str): Path to the virtual environment directory
        """
        self.venv_path = Path(venv_path)
        self.python_executable = self._get_python_executable()
        logger.info("cwd: %s", os.getcwd())

    def _get_python_executable(self):
        """Get the Python executable path for the target venv."""
        if sys.platform == "win32":
            python_exe = self.venv_path / "Scripts" / "python.exe"
        else:
            python_exe = self.venv_path / "bin" / "python"

        if not python_exe.exists():
            raise FileNotFoundError(f"Python executable not found at {python_exe}")

        return str(python_exe)

    def install_requirements(self, requirements_file: str) -> None:
        """
        Install Python requirements from a file in the target venv.
        Args:
            requirements_file (str): Path to the requirements file.
        """
        requirements_path = Path(requirements_file)
        if requirements_path.exists():
            rc = subprocess.check_call([self.python_executable, "-m", "pip", "install", "-r", requirements_file])
            if rc != 0:
                raise Exception(f"Failed to install requirements from {requirements_file}")

    def send_task(self, script_path: str, task_data: Any) -> Any:
        """
        Send a task to child process and get response.

        Args:
            script_path (str): Path to the child script
            task_data (dict): Data to send to child process

        Returns:
            dict: Response from child process
        """
        process = None
        try:
            # Prepare input data as JSON
            input_json = orjson.dumps(task_data).decode()
            # Start child process
            process = subprocess.Popen(
                [self.python_executable, script_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=os.getcwd(),  # Maintain current working directory
            )

            # Send data and get response
            stdout, stderr = process.communicate(input=input_json, timeout=30)

            if process.returncode != 0:
                raise RuntimeError(f"Child process failed: {stderr}")

            # Parse response
            try:
                response = json.loads(stdout.strip())
                return response
            except json.JSONDecodeError:
                raise RuntimeError(f"Invalid JSON response from child: {stdout}")

        except subprocess.TimeoutExpired:
            if process:
                process.kill()
            raise RuntimeError("Child process timed out")
        except Exception as e:
            raise RuntimeError(f"Communication error: {e}")
