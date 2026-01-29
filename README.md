## What's Cpex?

Cpex is a hook-driven plugin framework that allows applications to define extension points (hooks) and register plugins that are automatically invoked before and after critical execution points. It provides a robust mechanism for managing plugin lifecycles, enforcing execution timeouts, and handling plugin violations in a controlled and predictable way.

## Overview

Cpex introduces a lightweight, stack-agnostic hook system that intercepts execution points such as prompt handling, tool invocation, and data transformation, allowing plugins to observe, enforce, or modify behavior while remaining minimally invasive to host runtimes and application logic.

The framework enables you to:

- **Define custom hook points** throughout your application (these are the extension points to which plugins are registered)
- **Create plugins** that execute at these hook points
- **Control execution** with priorities, conditions, and modes (e.g., enforce, permissive)
- **Manage plugin lifecycle** with initialization and shutdown hooks
- **Handle errors gracefully** with timeout protection and error isolation
- **Deploy plugins** natively with the application or as an external service

## Architecture

### Core Components

- **Plugin**: Base class for implementing plugin logic
- **PluginManager**: Manages plugin lifecycle and orchestrates execution
- **PluginRegistry**: Maintains loaded plugin instances and hook mappings
- **HookRegistry**: Maps hook types to their payload/result Pydantic models
- **PluginConfig**: Configuration model for plugin metadata and behavior

### Plugin Modes

- **ENFORCE**: Plugin violations block execution (production mode)
- **ENFORCE_IGNORE_ERROR**: Violations block, but errors are ignored
- **PERMISSIVE**: Plugin runs but violations don't block (audit mode)
- **DISABLED**: Plugin is not loaded or executed

## Configuration

### Plugin Configuration File

Create a YAML configuration file (e.g., \`plugins/config.yaml\`):

```yaml
plugin_settings:
  enable_plugin_api: true
  plugin_timeout: 30
  fail_on_plugin_error: false
  parallel_execution_within_band: false

plugin_dirs:
  - ./plugins

plugins:
  - name: my_validation_plugin
    description: Validates prompt inputs
    author: Your Name
    kind: my_app.plugins.ValidationPlugin
    version: 1.0.0
    hooks:
      - prompt_pre_fetch
    tags:
      - validation
      - security
    mode: enforce
    priority: 10
    config:
      max_length: 1000
      forbidden_words:
        - spam
        - test
```

### Configuration Fields

- **name**: Unique plugin identifier
- **description**: Human-readable description
- **author**: Plugin author
- **kind**: Fully qualified class name (e.g., \`my_app.plugins.MyPlugin\`)
- **version**: Semantic version
- **hooks**: List of hook types where plugin executes
- **tags**: Categorization tags
- **mode**: Execution mode (enforce/permissive/disabled)
- **priority**: Execution order (lower = higher priority)
- **config**: Plugin-specific configuration dictionary

## Creating Plugins

### 1. Define a New Hook Type (Base Plugin Class)

First, define the payload and result models for your hook:

```python
# my_app/plugins/models.py
from pydantic import BaseModel
from cpex.framework.models import PluginPayload, PluginResult

class MyHookPayload(PluginPayload):
    """Payload for my custom hook."""
    data: str
    metadata: dict[str, str] = {}

class MyHookResult(PluginResult[MyHookPayload]):
    """Result type for my custom hook."""
    pass
```

Create a base plugin class that defines the hook:

```python
# my_app/plugins/base.py
from cpex.framework.base import Plugin
from cpex.framework.models import PluginConfig, PluginContext
from my_app.plugins.models import MyHookPayload, MyHookResult

class MyAppPlugin(Plugin):
    """Base plugin for MyApp with custom hooks."""

    def __init__(self, config: PluginConfig) -> None:
        super().__init__(config)

    async def my_custom_hook(
        self,
        payload: MyHookPayload,
        context: PluginContext
    ) -> MyHookResult:
        """Custom hook for processing data.

        Args:
            payload: The data to process
            context: Execution context with state

        Returns:
            Result indicating whether to continue processing
        """
        raise NotImplementedError(
            f"'my_custom_hook' not implemented for plugin {self.name}"
        )
```

### 2. Register Your Hook Types

Register your hooks in the global hook registry:

```python
# my_app/plugins/__init__.py
from cpex.framework.hook_registry import get_hook_registry
from my_app.plugins.models import MyHookPayload, MyHookResult

# Register your hook types
registry = get_hook_registry()
registry.register_hook(
    hook_type="my_custom_hook",
    payload_class=MyHookPayload,
    result_class=MyHookResult
)
```

### 3. Implement a Plugin

Create a concrete plugin implementation:

```python
# my_app/plugins/validation.py
from cpex.framework.models import PluginConfig, PluginContext, PluginViolation
from my_app.plugins.base import MyAppPlugin
from my_app.plugins.models import MyHookPayload, MyHookResult

class ValidationPlugin(MyAppPlugin):
    """Plugin that validates data."""

    def __init__(self, config: PluginConfig) -> None:
        super().__init__(config)
        # Access plugin-specific config
        self.max_length = config.config.get("max_length", 1000)
        self.forbidden_words = config.config.get("forbidden_words", [])

    async def my_custom_hook(
        self,
        payload: MyHookPayload,
        context: PluginContext
    ) -> MyHookResult:
        """Validate the data."""
        data = payload.data

        # Check length
        if len(data) > self.max_length:
            return MyHookResult(
                continue_processing=False,
                violation=PluginViolation(
                    reason="Data too long",
                    description=f"Data length {len(data)} exceeds max {self.max_length}",
                    code="DATA_TOO_LONG",
                    details={"length": len(data), "max": self.max_length}
                )
            )

        # Check for forbidden words
        for word in self.forbidden_words:
            if word.lower() in data.lower():
                return MyHookResult(
                    continue_processing=False,
                    violation=PluginViolation(
                        reason="Forbidden content",
                        description=f"Data contains forbidden word: {word}",
                        code="FORBIDDEN_WORD",
                        details={"word": word}
                    )
                )

        # Store state for later hooks
        context.set_state("validated", True)

        # All checks passed
        return MyHookResult(
            continue_processing=True,
            metadata={"validated_by": self.name}
        )
```

## Using the Plugin Manager

### Basic Usage

```python
from cpex.framework import PluginManager
from cpex.framework.models import GlobalContext
from my_app.plugins.models import MyHookPayload

# Initialize the manager with config file
manager = PluginManager("plugins/config.yaml")

# Initialize plugins (loads and registers all plugins)
await manager.initialize()

# Create a global context for the request
context = GlobalContext(
    request_id="req-123",
    user="alice",
    tenant_id="tenant-1",
    server_id="server-1"
)

# Create your payload
payload = MyHookPayload(
    data="Hello, world!",
    metadata={"source": "api"}
)

# Execute all plugins registered for this hook
result, plugin_contexts = await manager.invoke_hook(
    hook_type="my_custom_hook",
    payload=payload,
    global_context=context
)

# Check result
if result.continue_processing:
    # Use modified payload if any
    processed_data = result.modified_payload.data if result.modified_payload else payload.data
    print(f"Processed: {processed_data}")
    print(f"Metadata: {result.metadata}")
else:
    # Handle violation
    if result.violation:
        print(f"Blocked: {result.violation.reason}")
        print(f"Details: {result.violation.details}")

# Shutdown when done
await manager.shutdown()
```

### Advanced Usage

#### Invoke a Specific Plugin

```python
from cpex.framework.models import PluginContext

# Create a plugin-specific context
plugin_context = PluginContext(global_context=context)

# Invoke a specific plugin by name
result = await manager.invoke_hook_for_plugin(
    name="my_validation_plugin",
    hook_type="my_custom_hook",
    payload=payload,
    context=plugin_context,
    violations_as_exceptions=True  # Raise exceptions on violations
)
```

#### Use Plugin Conditions

Configure plugins to only run under certain conditions:

```yaml
plugins:
  - name: tenant_specific_plugin
    kind: my_app.plugins.TenantPlugin
    hooks:
      - my_custom_hook
    mode: enforce
    conditions:
      - tenant_ids:
          - tenant-1
          - tenant-2
        server_ids:
          - server-prod
```

#### Modify Payloads

Plugins can transform payloads:

```python
async def my_custom_hook(self, payload: MyHookPayload, context: PluginContext) -> MyHookResult:
    # Modify the payload
    modified_payload = MyHookPayload(
        data=payload.data.upper(),
        metadata=payload.metadata
    )

    return MyHookResult(
        continue_processing=True,
        modified_payload=modified_payload
    )
```

## MCP Plugin Example

The framework includes built-in support for MCP (Model Context Protocol) plugins with pre-defined hooks:

```python
from cpex.framework import PluginConfig, PluginMode
from cpex.mcp.entities import MCPPlugin, HookType
from cpex.mcp.entities.models import (
    PromptPrehookPayload,
    PromptPrehookResult
)

class PromptFilterPlugin(MCPPlugin):
    """Filter prompts before they're rendered."""

    async def prompt_pre_fetch(
        self,
        payload: PromptPrehookPayload,
        context: PluginContext
    ) -> PromptPrehookResult:
        """Check prompt name against allowed list."""
        allowed_prompts = self.config.config.get("allowed_prompts", [])

        if payload.name not in allowed_prompts:
            return PromptPrehookResult(
                continue_processing=False,
                violation=PluginViolation(
                    reason="Prompt not allowed",
                    description=f"Prompt '{payload.name}' is not in allowed list",
                    code="PROMPT_NOT_ALLOWED",
                    details={"prompt": payload.name}
                )
            )

        return PromptPrehookResult(continue_processing=True)

# Configure in YAML
# plugins:
#   - name: prompt_filter
#     kind: my_app.plugins.PromptFilterPlugin
#     hooks:
#       - prompt_pre_fetch
#     mode: enforce
#     config:
#       allowed_prompts:
#         - greeting
#         - help
```

## Available MCP Hook Types

The framework provides these built-in MCP hooks:

- **prompt_pre_fetch**: Before prompt template is rendered
- **prompt_post_fetch**: After prompt template is rendered
- **tool_pre_invoke**: Before tool is invoked
- **tool_post_invoke**: After tool execution completes
- **resource_pre_fetch**: Before resource is fetched
- **resource_post_fetch**: After resource is fetched

## External Plugins

The framework supports external plugins via the MCP protocol:

```yaml
plugins:
  - name: remote_validator
    kind: external
    hooks:
      - prompt_pre_fetch
    mode: enforce
    mcp:
      proto: STREAMABLEHTTP
      url: https://plugin-server.example.com
      tls:
        certfile: /path/to/client-cert.pem
        keyfile: /path/to/client-key.pem
        ca_bundle: /path/to/ca-bundle.pem
        verify: true
```

## Testing

### Unit Testing Plugins

```python
import pytest
from Cpex.framework.models import PluginConfig, GlobalContext, PluginContext
from my_app.plugins.validation import ValidationPlugin
from my_app.plugins.models import MyHookPayload

@pytest.mark.asyncio
async def test_validation_plugin():
    config = PluginConfig(
        name="test_validator",
        kind="my_app.plugins.ValidationPlugin",
        version="1.0.0",
        hooks=["my_custom_hook"],
        config={
            "max_length": 10,
            "forbidden_words": ["bad"]
        }
    )

    plugin = ValidationPlugin(config)

    # Test valid data
    payload = MyHookPayload(data="good")
    context = PluginContext(
        global_context=GlobalContext(request_id="test-1")
    )

    result = await plugin.my_custom_hook(payload, context)
    assert result.continue_processing is True

    # Test invalid data
    payload = MyHookPayload(data="this is too long")
    result = await plugin.my_custom_hook(payload, context)
    assert result.continue_processing is False
    assert result.violation.code == "DATA_TOO_LONG"
```

## Best Practices

1. **Use descriptive names** for plugins and hooks
2. **Set appropriate priorities** (10-20 for validation, 50 for transformation, 90 for logging)
3. **Use ENFORCE mode** in production for critical plugins
4. **Use PERMISSIVE mode** during development and testing
5. **Keep plugins focused** on a single responsibility
6. **Document plugin behavior** in configuration and docstrings
7. **Handle errors gracefully** and provide clear violation messages
8. **Use plugin context** to share state between hooks
9. **Test plugins independently** before integration
10. **Monitor plugin performance** and set appropriate timeouts

## Directory Structure

```
my_app/
├── plugins/
│   ├── __init__.py          # Hook registration
│   ├── base.py              # Base plugin class with custom hooks
│   ├── models.py            # Payload and result models
│   ├── validation.py        # Validation plugin
│   └── transformation.py    # Transformation plugin
├── config/
│   └── plugins.yaml         # Plugin configuration
└── main.py                  # Application entry point
```

## License

Apache-2.0