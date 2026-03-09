# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/framework/isolated/test_venv_comm.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Ted Habeck

Unit tests for VenvProcessCommunicator.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from cpex.framework.isolated.venv_comm import VenvProcessCommunicator


class TestVenvProcessCommunicator:
    """Test suite for VenvProcessCommunicator class."""

    @pytest.fixture
    def mock_venv_path(self, tmp_path):
        """Create a mock venv directory structure."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()
        
        # Create appropriate bin/Scripts directory based on platform
        if sys.platform == "win32":
            scripts_dir = venv_path / "Scripts"
            scripts_dir.mkdir()
            python_exe = scripts_dir / "python.exe"
        else:
            bin_dir = venv_path / "bin"
            bin_dir.mkdir()
            python_exe = bin_dir / "python"
        
        # Create a dummy python executable
        python_exe.touch()
        python_exe.chmod(0o755)
        
        return venv_path

    @pytest.fixture
    def communicator(self, mock_venv_path):
        """Create a VenvProcessCommunicator instance with mock venv."""
        return VenvProcessCommunicator(str(mock_venv_path))

    def test_init_valid_venv(self, mock_venv_path):
        """Test initialization with valid venv path."""
        comm = VenvProcessCommunicator(str(mock_venv_path))
        assert comm.venv_path == mock_venv_path
        assert comm.python_executable is not None
        assert Path(comm.python_executable).exists()

    def test_init_invalid_venv(self, tmp_path):
        """Test initialization with invalid venv path raises error."""
        invalid_path = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError, match="Python executable not found"):
            VenvProcessCommunicator(str(invalid_path))

    def test_get_python_executable_unix(self, tmp_path):
        """Test getting Python executable path on Unix-like systems."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()
        bin_dir = venv_path / "bin"
        bin_dir.mkdir()
        python_exe = bin_dir / "python"
        python_exe.touch()
        
        with patch("sys.platform", "linux"):
            comm = VenvProcessCommunicator(str(venv_path))
            assert comm.python_executable == str(python_exe)

    def test_get_python_executable_windows(self, tmp_path):
        """Test getting Python executable path on Windows."""
        venv_path = tmp_path / ".venv"
        venv_path.mkdir()
        scripts_dir = venv_path / "Scripts"
        scripts_dir.mkdir()
        python_exe = scripts_dir / "python.exe"
        python_exe.touch()
        
        with patch("sys.platform", "win32"):
            comm = VenvProcessCommunicator(str(venv_path))
            assert comm.python_executable == str(python_exe)

    @patch("subprocess.check_call")
    def test_install_requirements_success(self, mock_check_call, communicator, tmp_path):
        """Test successful requirements installation."""
        requirements_file = tmp_path / "requirements.txt"
        requirements_file.write_text("pytest>=7.0.0\n")
        
        mock_check_call.return_value = 0
        
        communicator.install_requirements(str(requirements_file))
        
        mock_check_call.assert_called_once_with([
            communicator.python_executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(requirements_file)
        ])

    @patch("subprocess.check_call")
    def test_install_requirements_failure(self, mock_check_call, communicator, tmp_path):
        """Test requirements installation failure."""
        requirements_file = tmp_path / "requirements.txt"
        requirements_file.write_text("invalid-package-name-xyz\n")
        
        mock_check_call.return_value = 1
        
        with pytest.raises(Exception, match="Failed to install requirements"):
            communicator.install_requirements(str(requirements_file))

    def test_install_requirements_nonexistent_file(self, communicator):
        """Test install_requirements with nonexistent file does nothing."""
        # Should not raise an error if file doesn't exist
        communicator.install_requirements("nonexistent_requirements.txt")

    @patch("subprocess.Popen")
    def test_send_task_success(self, mock_popen, communicator):
        """Test successful task sending and response."""
        task_data = {"task_type": "info", "data": "test"}
        expected_response = {"status": "success", "result": "ok"}
        
        # Mock the process
        mock_process = MagicMock()
        mock_process.communicate.return_value = (json.dumps(expected_response), "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        
        result = communicator.send_task("test_script.py", task_data)
        
        assert result == expected_response
        mock_popen.assert_called_once()
        mock_process.communicate.assert_called_once()

    @patch("subprocess.Popen")
    def test_send_task_process_failure(self, mock_popen, communicator):
        """Test task sending with process failure."""
        task_data = {"task_type": "test"}
        
        mock_process = MagicMock()
        mock_process.communicate.return_value = ("", "Error occurred")
        mock_process.returncode = 1
        mock_popen.return_value = mock_process
        
        with pytest.raises(RuntimeError, match="Child process failed"):
            communicator.send_task("test_script.py", task_data)

    @patch("subprocess.Popen")
    def test_send_task_invalid_json_response(self, mock_popen, communicator):
        """Test task sending with invalid JSON response."""
        task_data = {"task_type": "test"}
        
        mock_process = MagicMock()
        mock_process.communicate.return_value = ("invalid json", "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        
        with pytest.raises(RuntimeError, match="Invalid JSON response"):
            communicator.send_task("test_script.py", task_data)

    @patch("subprocess.Popen")
    def test_send_task_timeout(self, mock_popen, communicator):
        """Test task sending with timeout."""
        task_data = {"task_type": "test"}
        
        mock_process = MagicMock()
        mock_process.communicate.side_effect = subprocess.TimeoutExpired("cmd", 30)
        mock_popen.return_value = mock_process
        
        with pytest.raises(RuntimeError, match="Child process timed out"):
            communicator.send_task("test_script.py", task_data)
        
        mock_process.kill.assert_called_once()

    @patch("subprocess.Popen")
    def test_send_task_communication_error(self, mock_popen, communicator):
        """Test task sending with communication error."""
        task_data = {"task_type": "test"}
        
        mock_popen.side_effect = OSError("Connection failed")
        
        with pytest.raises(RuntimeError, match="Communication error"):
            communicator.send_task("test_script.py", task_data)

    @patch("subprocess.Popen")
    def test_send_task_with_complex_data(self, mock_popen, communicator):
        """Test sending task with complex nested data structures."""
        task_data = {
            "task_type": "load_and_run_hook",
            "config": {"nested": {"data": [1, 2, 3]}},
            "payload": {"args": {"key": "value"}},
            "context": {"state": {}, "metadata": {}}
        }
        expected_response = {"status": "success", "result": {"data": "processed"}}
        
        mock_process = MagicMock()
        mock_process.communicate.return_value = (json.dumps(expected_response), "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        
        result = communicator.send_task("worker.py", task_data)
        
        assert result == expected_response
        # Verify the task was serialized properly
        call_args = mock_popen.call_args
        assert call_args is not None

    @patch("subprocess.Popen")
    @patch("os.getcwd")
    def test_send_task_maintains_cwd(self, mock_getcwd, mock_popen, communicator):
        """Test that send_task maintains current working directory."""
        mock_getcwd.return_value = "/test/path"
        task_data = {"task_type": "test"}
        
        mock_process = MagicMock()
        mock_process.communicate.return_value = ('{"status": "ok"}', "")
        mock_process.returncode = 0
        mock_popen.return_value = mock_process
        
        communicator.send_task("test_script.py", task_data)
        
        # Verify cwd was passed to Popen
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["cwd"] == "/test/path"

    def test_python_executable_property(self, communicator):
        """Test that python_executable property is accessible."""
        assert communicator.python_executable is not None
        assert isinstance(communicator.python_executable, str)
        assert Path(communicator.python_executable).exists()

    def test_venv_path_property(self, communicator, mock_venv_path):
        """Test that venv_path property is accessible."""
        assert communicator.venv_path == mock_venv_path
        assert isinstance(communicator.venv_path, Path)

# Made with Bob
