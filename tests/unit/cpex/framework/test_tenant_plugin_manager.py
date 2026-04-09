# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/framework/test_tenant_plugin_manager.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0

Tests for TenantPluginManager and TenantPluginManagerFactory.

Ported from ContextForge's test_tenant_plugin_manager_tool_scoped.py,
demonstrating that the factory can be used with any context identifier
to create isolated plugin manager instances.
"""

# Standard
import asyncio
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, Mock, patch

# Third-Party
import pytest

# First-Party
from cpex.framework.loader.config import ConfigLoader
from cpex.framework.manager import TenantPluginManager, TenantPluginManagerFactory
from cpex.framework.models import PluginConfig, PluginConfigOverride, PluginMode
from cpex.framework.observability import ObservabilityProvider

FIXTURE_NO_PLUGIN = "./tests/unit/cpex/fixtures/configs/valid_no_plugin.yaml"


class ToolScopedPluginManagerFactory(TenantPluginManagerFactory):
    """Factory that uses tool_id as context for plugin configuration."""

    def __init__(self, yaml_path: str, tool_configs: Optional[dict[str, list[PluginConfigOverride]]] = None):
        super().__init__(yaml_path=yaml_path)
        self._tool_configs = tool_configs or {}

    async def get_config_from_db(self, context_id: str) -> Optional[list[PluginConfigOverride]]:
        return self._tool_configs.get(context_id)


@pytest.mark.asyncio
async def test_factory_with_tool_id_scoping():
    """Test that factory can use tool_id as context for isolated plugin managers."""
    tool_configs = {
        "tool_calculator": [
            PluginConfigOverride(name="ArgumentNormalizer", mode=PluginMode.SEQUENTIAL, priority=10, config={"normalize_numbers": True})
        ],
        "tool_file_reader": [
            PluginConfigOverride(name="ArgumentNormalizer", mode=PluginMode.AUDIT, priority=50, config={"normalize_paths": True})
        ],
    }
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN, tool_configs=tool_configs)

    try:
        calc_manager = await factory.get_manager(context_id="tool_calculator")
        assert calc_manager is not None
        assert calc_manager.initialized

        file_manager = await factory.get_manager(context_id="tool_file_reader")
        assert file_manager is not None
        assert file_manager.initialized
        assert calc_manager is not file_manager

        # Cached
        calc_manager_2 = await factory.get_manager(context_id="tool_calculator")
        assert calc_manager_2 is calc_manager
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_with_default_context():
    """Test that factory uses default context when no context_id is provided."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        default_manager = await factory.get_manager()
        assert default_manager is not None
        assert default_manager.initialized

        default_manager_2 = await factory.get_manager(context_id=None)
        assert default_manager_2 is default_manager
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_reload_context():
    """Test that factory can reload a context-specific manager."""
    tool_configs = {
        "tool_api_client": [PluginConfigOverride(name="ArgumentNormalizer", mode=PluginMode.SEQUENTIAL, priority=20)],
    }
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN, tool_configs=tool_configs)

    try:
        manager1 = await factory.get_manager(context_id="tool_api_client")
        assert manager1 is not None

        manager2 = await factory.reload_tenant(context_id="tool_api_client")
        assert manager2 is not None
        assert manager2 is not manager1

        manager3 = await factory.get_manager(context_id="tool_api_client")
        assert manager3 is manager2
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_concurrent_different_contexts():
    """Test that factory handles concurrent access to different contexts safely."""
    tool_configs = {f"tool_{i}": [PluginConfigOverride(name="Norm", mode=PluginMode.SEQUENTIAL, priority=i * 10)] for i in range(5)}
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN, tool_configs=tool_configs)

    try:
        tasks = [factory.get_manager(context_id=f"tool_{i}") for i in range(5)]
        managers = await asyncio.gather(*tasks)

        assert len(managers) == 5
        assert all(m is not None for m in managers)
        assert all(m.initialized for m in managers)
        assert len(set(id(m) for m in managers)) == 5
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_concurrent_same_context():
    """Test that concurrent requests for same context return same manager."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        tasks = [factory.get_manager(context_id="same_tool") for _ in range(5)]
        managers = await asyncio.gather(*tasks)

        assert len(set(id(m) for m in managers)) == 1
        assert all(m.initialized for m in managers)
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_context_without_overrides():
    """Test that factory works for contexts without specific overrides."""
    tool_configs = {"tool_with_config": [PluginConfigOverride(name="Norm", mode=PluginMode.SEQUENTIAL)]}
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN, tool_configs=tool_configs)

    try:
        manager = await factory.get_manager(context_id="tool_without_config")
        assert manager is not None
        assert manager.initialized
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_tenant_plugin_manager_with_config_object():
    """Test TenantPluginManager initialization with Config object."""
    config = ConfigLoader.load_config(FIXTURE_NO_PLUGIN)
    manager = TenantPluginManager(config=config)
    try:
        await manager.initialize()
        assert manager.initialized
        assert manager._config_path is None
        assert manager._config is config
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_tenant_plugin_manager_with_string_path():
    """Test TenantPluginManager initialization with string path."""
    manager = TenantPluginManager(config=FIXTURE_NO_PLUGIN)
    try:
        await manager.initialize()
        assert manager.initialized
        assert manager._config_path == FIXTURE_NO_PLUGIN
        assert manager._config is not None
    finally:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_factory_observability_setter():
    """Test that observability setter updates the provider."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        assert factory.observability is None

        mock_provider = Mock(spec=ObservabilityProvider)
        factory.observability = mock_provider
        assert factory.observability is mock_provider

        factory.observability = None
        assert factory.observability is None
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_build_manager_cancelled():
    """Test that cancelled build task properly cleans up."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        with patch("cpex.framework.manager.TenantPluginManager.initialize", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await factory.get_manager(context_id="cancelled_tool")

            assert "cancelled_tool" not in factory._inflight
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_build_manager_exception():
    """Test that exception during build properly cleans up."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        with patch("cpex.framework.manager.TenantPluginManager.initialize", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = RuntimeError("Init failed")

            with pytest.raises(RuntimeError, match="Init failed"):
                await factory.get_manager(context_id="error_tool")

            assert "error_tool" not in factory._inflight
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_merge_tenant_config_none():
    """Test _merge_tenant_config with None override returns base config."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        merged = factory._merge_tenant_config(None)
        assert merged is factory._base_config
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_reload_shutdown_exception():
    """Test that reload handles old manager shutdown exception gracefully."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        manager1 = await factory.get_manager(context_id="reload_error_tool")
        assert manager1 is not None

        with patch.object(manager1, "shutdown", new_callable=AsyncMock) as mock_shutdown:
            mock_shutdown.side_effect = RuntimeError("Shutdown failed")
            manager2 = await factory.reload_tenant(context_id="reload_error_tool")
            assert manager2 is not None
            assert manager2 is not manager1
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_shutdown_empty():
    """Test shutdown with no inflight tasks."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    await factory.get_manager(context_id="tool1")
    await factory.get_manager(context_id="tool2")

    await factory.shutdown()

    assert len(factory._managers) == 0
    assert len(factory._inflight) == 0


@pytest.mark.asyncio
async def test_factory_shutdown_with_exceptions():
    """Test that shutdown handles manager shutdown exceptions gracefully."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    manager1 = await factory.get_manager(context_id="tool1")
    await factory.get_manager(context_id="tool2")

    with patch.object(manager1, "shutdown", new_callable=AsyncMock) as mock_shutdown1:
        mock_shutdown1.side_effect = RuntimeError("Shutdown failed")
        await factory.shutdown()
        assert mock_shutdown1.called


@pytest.mark.asyncio
async def test_factory_get_config_from_db_default():
    """Test that default get_config_from_db returns None."""
    factory = TenantPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        result = await factory.get_config_from_db("any_context")
        assert result is None
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_merge_config_with_mode_and_priority():
    """Test _merge_tenant_config properly handles mode and priority overrides."""
    base_config = ConfigLoader.load_config(FIXTURE_NO_PLUGIN)

    test_plugin = PluginConfig(
        name="TestPlugin",
        kind="test.plugin.TestPlugin",
        hooks=["prompt_pre_fetch"],
        mode=PluginMode.SEQUENTIAL,
        priority=50,
        config={"base_key": "base_value"},
    )
    base_config.plugins = [test_plugin]

    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)
    factory._base_config = base_config

    try:
        override = PluginConfigOverride(
            name="TestPlugin",
            mode=PluginMode.AUDIT,
            priority=99,
            config={"override_key": "override_value"},
        )

        merged = factory._merge_tenant_config([override])
        assert merged is not None
        assert len(merged.plugins) == 1

        merged_plugin = merged.plugins[0]
        assert merged_plugin.mode == PluginMode.AUDIT
        assert merged_plugin.priority == 99
        assert "base_key" in merged_plugin.config
        assert "override_key" in merged_plugin.config
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_merge_config_no_override_for_plugin():
    """Test _merge_tenant_config keeps original plugin when no override exists."""
    base_config = ConfigLoader.load_config(FIXTURE_NO_PLUGIN)

    test_plugin = PluginConfig(
        name="TestPlugin",
        kind="test.plugin.TestPlugin",
        hooks=["prompt_pre_fetch"],
        mode=PluginMode.SEQUENTIAL,
        priority=50,
        config={"base_key": "base_value"},
    )
    base_config.plugins = [test_plugin]

    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)
    factory._base_config = base_config

    try:
        override = PluginConfigOverride(name="DifferentPlugin", mode=PluginMode.AUDIT, priority=99)
        merged = factory._merge_tenant_config([override])
        assert merged is not None
        assert len(merged.plugins) == 1

        merged_plugin = merged.plugins[0]
        assert merged_plugin.name == "TestPlugin"
        assert merged_plugin.mode == PluginMode.SEQUENTIAL
        assert merged_plugin.priority == 50
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_build_cancelled_with_shutdown_error():
    """Test _build_manager handles CancelledError with shutdown exception."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        manager_mock = MagicMock()
        manager_mock.shutdown = AsyncMock(side_effect=RuntimeError("Shutdown failed"))

        with patch("cpex.framework.manager.TenantPluginManager") as MockManager:
            MockManager.return_value = manager_mock
            manager_mock.initialize = AsyncMock(side_effect=asyncio.CancelledError())

            with pytest.raises(asyncio.CancelledError):
                await factory.get_manager(context_id="cancelled_with_error")

            manager_mock.shutdown.assert_called_once()
    finally:
        await factory.shutdown()


@pytest.mark.asyncio
async def test_factory_build_exception_with_shutdown_error():
    """Test _build_manager handles Exception with shutdown exception."""
    factory = ToolScopedPluginManagerFactory(yaml_path=FIXTURE_NO_PLUGIN)

    try:
        manager_mock = MagicMock()
        manager_mock.shutdown = AsyncMock(side_effect=RuntimeError("Shutdown failed"))

        with patch("cpex.framework.manager.TenantPluginManager") as MockManager:
            MockManager.return_value = manager_mock
            manager_mock.initialize = AsyncMock(side_effect=ValueError("Init failed"))

            with pytest.raises(ValueError, match="Init failed"):
                await factory.get_manager(context_id="error_with_shutdown_error")

            manager_mock.shutdown.assert_called_once()
    finally:
        await factory.shutdown()
