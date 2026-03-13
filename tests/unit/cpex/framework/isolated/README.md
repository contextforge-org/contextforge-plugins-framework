# Isolated Plugin Framework Tests

This directory contains comprehensive unit and integration tests for the isolated plugin framework, which enables running plugins in separate Python virtual environments.

## Overview

The isolated plugin framework consists of three main components:

1. **VenvProcessCommunicator** (`venv_comm.py`) - Handles communication with child processes in different virtual environments
2. **IsolatedVenvPlugin** (`client.py`) - Plugin client that manages venv-isolated plugins
3. **Worker** (`worker.py`) - Worker process that runs inside the venv and executes plugin hooks

## Test Files

### `test_venv_comm.py`
Tests for the `VenvProcessCommunicator` class that handles inter-process communication.

**Coverage:**
- Virtual environment path validation (Unix/Windows)
- Python executable detection
- Requirements installation (success/failure cases)
- Task sending and response handling
- Error handling (timeouts, invalid JSON, process failures)
- Complex data serialization
- Working directory maintenance

**Key Test Cases:**
- `test_init_valid_venv` - Validates proper initialization with valid venv
- `test_send_task_success` - Tests successful task execution
- `test_send_task_timeout` - Tests timeout handling
- `test_install_requirements_success` - Tests pip installation

### `test_client.py`
Tests for the `IsolatedVenvPlugin` class that serves as the plugin client.

**Coverage:**
- Plugin initialization and configuration
- Virtual environment creation
- Hook invocation for all hook types (tool_pre_invoke, tool_post_invoke, prompt_pre_fetch, prompt_post_fetch)
- Payload and context serialization
- Error handling (PluginError, generic exceptions)
- Policy violation handling
- Safe config generation

**Key Test Cases:**
- `test_invoke_hook_tool_pre_invoke_success` - Tests tool pre-invoke hook
- `test_invoke_hook_with_violation` - Tests policy violation handling
- `test_invoke_hook_plugin_error` - Tests PluginError propagation
- `test_invoke_hook_serialization` - Tests proper data serialization

### `test_worker.py`
Tests for the worker process functions that execute inside the venv.

**Coverage:**
- Environment information retrieval
- Plugin configuration loading
- Task processing (info, load_and_run_hook)
- Plugin loading and instantiation
- Hook execution
- Error handling (import errors, missing configs)
- Multiple hook type support
- sys.path modification

**Key Test Cases:**
- `test_get_environment_info` - Tests environment info collection
- `test_process_task_load_and_run_hook_success` - Tests successful hook execution
- `test_process_task_with_different_hook_types` - Tests all hook types
- `test_process_task_import_error` - Tests import error handling

### `test_integration.py`
Integration tests that verify the entire isolated plugin system working together.

**Coverage:**
- Full plugin lifecycle (initialization → hook invocation → cleanup)
- PluginManager integration with isolated plugins
- Context propagation through the isolation boundary
- Multiple hook type execution
- Policy violation handling end-to-end
- Error handling across process boundaries

**Key Test Cases:**
- `test_isolated_plugin_full_lifecycle` - Tests complete plugin lifecycle
- `test_isolated_plugin_context_propagation` - Tests context serialization
- `test_isolated_plugin_with_multiple_hooks` - Tests multiple hook types
- `test_isolated_plugin_violation_handling` - Tests violation propagation

### `conftest.py`
Pytest fixtures shared across all isolated plugin tests.

**Fixtures:**
- `mock_venv_structure` - Creates mock venv directory structure
- `sample_plugin_config` - Provides sample plugin configuration
- `sample_global_context` - Creates test GlobalContext
- `sample_plugin_context` - Creates test PluginContext
- `mock_communicator` - Provides mock VenvProcessCommunicator
- `sample_requirements_file` - Creates test requirements.txt

## Running the Tests

### Run all isolated plugin tests:
```bash
pytest tests/unit/cpex/framework/isolated/
```

### Run specific test file:
```bash
pytest tests/unit/cpex/framework/isolated/test_venv_comm.py
```

### Run with coverage:
```bash
pytest tests/unit/cpex/framework/isolated/ --cov=cpex.framework.isolated --cov-report=html
```

### Run specific test:
```bash
pytest tests/unit/cpex/framework/isolated/test_client.py::TestIsolatedVenvPlugin::test_invoke_hook_tool_pre_invoke_success
```

## Test Architecture

### Mocking Strategy
The tests use extensive mocking to avoid:
- Creating actual virtual environments (slow and resource-intensive)
- Installing real packages via pip
- Spawning actual subprocesses
- File system operations where possible

### Fixtures
Common test fixtures are defined in `conftest.py` to promote code reuse and consistency across tests.

### Test Organization
Tests are organized by component:
- **Unit tests** - Test individual functions and methods in isolation
- **Integration tests** - Test components working together

## Coverage Goals

The test suite aims for:
- **Line coverage**: >90%
- **Branch coverage**: >85%
- **Function coverage**: 100%

## Key Testing Patterns

### 1. Async Testing
```python
@pytest.mark.asyncio
async def test_async_function():
    result = await some_async_function()
    assert result is not None
```

### 2. Mock Subprocess Communication
```python
@patch("subprocess.Popen")
def test_send_task(mock_popen):
    mock_process = MagicMock()
    mock_process.communicate.return_value = ('{"status": "ok"}', "")
    mock_popen.return_value = mock_process
    # Test code here
```

### 3. Context Propagation Testing
```python
def test_context_propagation():
    # Create context with specific data
    context = PluginContext(global_context=GlobalContext(...))
    # Invoke hook
    result = await plugin.invoke_hook(hook_type, payload, context)
    # Verify context was properly serialized and sent
```

## Common Issues and Solutions

### Issue: Tests fail with "Python executable not found"
**Solution**: Ensure mock_venv_structure fixture is being used, which creates the proper directory structure.

### Issue: Async tests hang
**Solution**: Ensure all async functions are properly awaited and use `@pytest.mark.asyncio` decorator.

### Issue: Import errors in tests
**Solution**: Check that all required dependencies are installed in the test environment.

## Contributing

When adding new tests:
1. Follow the existing naming conventions (`test_<component>_<scenario>`)
2. Add docstrings explaining what the test validates
3. Use fixtures from `conftest.py` where applicable
4. Mock external dependencies (filesystem, network, subprocesses)
5. Test both success and failure paths
6. Update this README if adding new test files

## Related Documentation

- [Isolated Plugin Design](../../../../cpex/framework/isolated/design.md)
- [Plugin Framework Documentation](../../../../cpex/framework/README.md)
- [Main Test Suite](../../../README.md)