# -*- coding: utf-8 -*-
"""Location: ./tests/unit/cpex/framework/test_plugin_modes.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0

Tests for PluginMode / OnError refactor:
  - FIRE_AND_FORGET fire-and-forget semantics
  - OnError.IGNORE / DISABLE / FAIL behaviors
  - CONCURRENT parallel execution
  - SEQUENTIAL chained execution
  - AUDIT sequential execution (cannot halt)
  - Phase ordering: SEQUENTIAL → AUDIT → CONCURRENT → FIRE_AND_FORGET
  - execution_pool semaphore for FIRE_AND_FORGET tasks
  - Backward-compat migration: enforce / enforce_ignore_error → SEQUENTIAL, permissive → AUDIT
"""

# Standard
import asyncio
from unittest.mock import patch

# Third-Party
import pytest

from cpex.framework import (
    GlobalContext,
    OnError,
    Plugin,
    PluginConfig,
    PluginError,
    PluginManager,
    PluginMode,
    PluginResult,
    PromptHookType,
    PromptPrehookPayload,
)

# First-Party
from cpex.framework.base import HookRef
from cpex.framework.registry import PluginRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plugin_config(
    name: str, mode: PluginMode, on_error: OnError = OnError.FAIL, priority: int = 100
) -> PluginConfig:
    return PluginConfig(
        name=name,
        description="test",
        author="test",
        version="1.0",
        kind="test.Plugin",
        mode=mode,
        on_error=on_error,
        hooks=["prompt_pre_fetch"],
        tags=[],
        priority=priority,
    )


async def _make_manager() -> PluginManager:
    manager = PluginManager("./tests/unit/cpex/fixtures/configs/valid_no_plugin.yaml")
    await manager.initialize()
    return manager


# ---------------------------------------------------------------------------
# FIRE_AND_FORGET mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_and_forget_mode_fires_background_task():
    """Pipeline continues immediately without waiting for a FIRE_AND_FORGET plugin."""

    started = asyncio.Event()
    finished = asyncio.Event()

    class SlowFireAndForgetPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            started.set()
            await asyncio.sleep(0.05)
            finished.set()
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg = make_plugin_config("SlowFireAndForget", PluginMode.FIRE_AND_FORGET)
    plugin = SlowFireAndForgetPlugin(cfg)

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin))]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="1")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    # Pipeline result is returned immediately; FIRE_AND_FORGET task hasn't finished yet
    assert result.continue_processing
    assert not finished.is_set()

    # Let the background task complete
    await asyncio.sleep(0.1)
    assert finished.is_set()

    await manager.shutdown()


@pytest.mark.asyncio
async def test_fire_and_forget_mode_error_does_not_block():
    """A FIRE_AND_FORGET plugin that errors must not halt or affect the pipeline."""

    class BrokenFireAndForgetPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            raise RuntimeError("fire_and_forget error")

    manager = await _make_manager()
    cfg = make_plugin_config("BrokenFireAndForget", PluginMode.FIRE_AND_FORGET)
    plugin = BrokenFireAndForgetPlugin(cfg)

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin))]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="2")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing

    # Allow background task to run and silently fail
    await asyncio.sleep(0.05)

    await manager.shutdown()


# ---------------------------------------------------------------------------
# OnError behaviors (CONCURRENT mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_error_ignore_continues_pipeline():
    """CONCURRENT + on_error=IGNORE: error is logged, pipeline continues."""

    class FailPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            raise RuntimeError("intentional error")

    manager = await _make_manager()
    cfg = make_plugin_config("IgnorePlugin", PluginMode.CONCURRENT, on_error=OnError.IGNORE)
    plugin = FailPlugin(cfg)

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin))]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="3")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing
    assert result.violation is None

    await manager.shutdown()


@pytest.mark.asyncio
async def test_on_error_disable_disables_plugin():
    """CONCURRENT + on_error=DISABLE: plugin is runtime-disabled after first error."""

    call_count = 0

    class DisablePlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("first call fails")

    manager = await _make_manager()
    cfg = make_plugin_config("DisablePlugin", PluginMode.CONCURRENT, on_error=OnError.DISABLE)
    plugin = DisablePlugin(cfg)
    hook_ref = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [hook_ref]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="4")

        # First call: error is caught, plugin gets disabled
        result1, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)
        assert result1.continue_processing

        # Second call: plugin is in _runtime_disabled, should be skipped
        result2, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)
        assert result2.continue_processing

    # Plugin ran exactly once (skipped on second call)
    assert call_count == 1
    assert "DisablePlugin" in manager._executor._runtime_disabled

    await manager.shutdown()


@pytest.mark.asyncio
async def test_on_error_fail_raises():
    """CONCURRENT + on_error=FAIL (default): error propagates as PluginError."""

    class FailPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            raise RuntimeError("fail!")

    manager = await _make_manager()
    cfg = make_plugin_config("FailPlugin", PluginMode.CONCURRENT, on_error=OnError.FAIL)
    plugin = FailPlugin(cfg)

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin))]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="5")

        with pytest.raises(PluginError):
            await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    await manager.shutdown()


# ---------------------------------------------------------------------------
# CONCURRENT parallel execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_parallel_execution():
    """Multiple CONCURRENT plugins should run concurrently."""

    results_order: list[str] = []
    start_barrier = asyncio.Event()

    class SlowPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            results_order.append(f"start:{self.name}")
            await start_barrier.wait()
            await asyncio.sleep(0.01)
            results_order.append(f"end:{self.name}")
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg1 = make_plugin_config("ConcP1", PluginMode.CONCURRENT, priority=1)
    cfg2 = make_plugin_config("ConcP2", PluginMode.CONCURRENT, priority=2)
    plugin1 = SlowPlugin(cfg1)
    plugin2 = SlowPlugin(cfg2)

    ref1 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin1))
    ref2 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin2))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref1, ref2]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="6")

        # Let the invoke run while releasing the barrier concurrently
        async def release_and_invoke():
            task = asyncio.create_task(manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context))
            await asyncio.sleep(0.005)  # Let both plugins reach the barrier
            start_barrier.set()
            return await task

        result, _ = await release_and_invoke()

    assert result.continue_processing
    # Both plugins started before either finished (parallel execution)
    assert results_order[:2] == ["start:ConcP1", "start:ConcP2"]

    await manager.shutdown()


# ---------------------------------------------------------------------------
# AUDIT sequential execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_sequential_execution():
    """AUDIT plugins execute in priority order sequentially."""

    call_order: list[str] = []

    class SequentialPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            call_order.append(self.name)
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg1 = make_plugin_config("PermP1", PluginMode.AUDIT, priority=1)
    cfg2 = make_plugin_config("PermP2", PluginMode.AUDIT, priority=2)
    plugin1 = SequentialPlugin(cfg1)
    plugin2 = SequentialPlugin(cfg2)

    ref1 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin1))
    ref2 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin2))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref1, ref2]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="7")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing
    assert call_order == ["PermP1", "PermP2"]

    await manager.shutdown()


# ---------------------------------------------------------------------------
# execution_pool semaphore
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_pool_semaphore():
    """With execution_pool=1, FIRE_AND_FORGET tasks are serialized."""

    concurrency_high_water = 0
    current_concurrent = 0

    class ConcurrencyProbePlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            nonlocal concurrency_high_water, current_concurrent
            current_concurrent += 1
            concurrency_high_water = max(concurrency_high_water, current_concurrent)
            await asyncio.sleep(0.02)
            current_concurrent -= 1
            return PluginResult(continue_processing=True)

    manager = await _make_manager()

    cfg1 = make_plugin_config("ObsPool1", PluginMode.FIRE_AND_FORGET, priority=1)
    cfg2 = make_plugin_config("ObsPool2", PluginMode.FIRE_AND_FORGET, priority=2)
    plugin1 = ConcurrencyProbePlugin(cfg1)
    plugin2 = ConcurrencyProbePlugin(cfg2)

    ref1 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin1))
    ref2 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin2))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref1, ref2]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="8")

        # Patch execution_pool=1 via the settings layer
        with patch("cpex.framework.manager.settings") as mock_settings:
            mock_settings.execution_pool = 1
            result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing

    # Allow all background FIRE_AND_FORGET tasks to complete
    await asyncio.sleep(0.1)

    # With pool=1, max concurrency should be 1
    assert concurrency_high_water <= 1

    await manager.shutdown()


# ---------------------------------------------------------------------------
# Backward compatibility: YAML migration
# ---------------------------------------------------------------------------


def test_plugin_config_migration_enforce_ignore_error():
    """Legacy 'enforce_ignore_error' mode migrates to SEQUENTIAL + on_error=ignore."""
    cfg = PluginConfig.model_validate(
        {
            "name": "legacy",
            "kind": "test.Plugin",
            "mode": "enforce_ignore_error",
        }
    )
    assert cfg.mode == PluginMode.SEQUENTIAL
    assert cfg.on_error == OnError.IGNORE


def test_plugin_config_migration_preserves_explicit_on_error():
    """Migration does not override an explicitly provided on_error value."""
    cfg = PluginConfig.model_validate(
        {
            "name": "legacy2",
            "kind": "test.Plugin",
            "mode": "enforce_ignore_error",
            "on_error": "disable",
        }
    )
    assert cfg.mode == PluginMode.SEQUENTIAL
    assert cfg.on_error == OnError.DISABLE


def test_plugin_config_migration_enforce_to_sequential():
    """Legacy 'enforce' mode migrates to SEQUENTIAL."""
    cfg = PluginConfig.model_validate(
        {
            "name": "legacy_enforce",
            "kind": "test.Plugin",
            "mode": "enforce",
        }
    )
    assert cfg.mode == PluginMode.SEQUENTIAL


def test_plugin_config_migration_permissive_to_audit():
    """Legacy 'permissive' mode migrates to AUDIT."""
    cfg = PluginConfig.model_validate(
        {
            "name": "legacy_permissive",
            "kind": "test.Plugin",
            "mode": "permissive",
        }
    )
    assert cfg.mode == PluginMode.AUDIT


# ---------------------------------------------------------------------------
# SEQUENTIAL mode tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_chains_payload():
    """Each SEQUENTIAL plugin receives the output payload of the previous plugin."""

    received_payloads: list = []

    class ChainPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            step = int(payload.args.get("step", "0"))
            received_payloads.append(step)
            new_payload = PromptPrehookPayload(prompt_id=payload.prompt_id, args={"step": str(step + 1)})
            return PluginResult(continue_processing=True, modified_payload=new_payload)

    manager = await _make_manager()
    cfg1 = make_plugin_config("SeqChain1", PluginMode.SEQUENTIAL, priority=1)
    cfg2 = make_plugin_config("SeqChain2", PluginMode.SEQUENTIAL, priority=2)
    plugin1 = ChainPlugin(cfg1)
    plugin2 = ChainPlugin(cfg2)

    ref1 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin1))
    ref2 = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin2))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref1, ref2]
        payload = PromptPrehookPayload(prompt_id="test", args={"step": "0"})
        global_context = GlobalContext(request_id="seq1")

        with patch("cpex.framework.manager.settings") as mock_settings:
            mock_settings.execution_pool = None
            mock_settings.default_hook_policy = "allow"
            result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    # First plugin saw step=0, second saw step=1 (chained)
    assert received_payloads[0] == 0
    assert received_payloads[1] == 1
    # Final payload has step=2
    assert result.modified_payload is not None
    assert result.modified_payload.args["step"] == "2"

    await manager.shutdown()


@pytest.mark.asyncio
async def test_sequential_can_halt_pipeline():
    """A SEQUENTIAL plugin that returns continue_processing=False halts the pipeline."""

    call_order: list[str] = []

    class HaltPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            call_order.append(self.name)
            return PluginResult(continue_processing=False)

    class NeverReachedPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            call_order.append(self.name)
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg_halt = make_plugin_config("SeqHalt", PluginMode.SEQUENTIAL, priority=1)
    cfg_after = make_plugin_config("SeqAfter", PluginMode.SEQUENTIAL, priority=2)
    plugin_halt = HaltPlugin(cfg_halt)
    plugin_after = NeverReachedPlugin(cfg_after)

    ref_halt = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_halt))
    ref_after = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_after))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref_halt, ref_after]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="seq2")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert not result.continue_processing
    assert call_order == ["SeqHalt"]  # second plugin never ran

    await manager.shutdown()


@pytest.mark.asyncio
async def test_sequential_executes_before_audit():
    """SEQUENTIAL plugins run before AUDIT plugins."""

    call_order: list[str] = []

    class OrderPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            call_order.append(self.name)
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg_seq = make_plugin_config("SeqFirst", PluginMode.SEQUENTIAL, priority=10)
    cfg_perm = make_plugin_config("PermSecond", PluginMode.AUDIT, priority=1)  # lower priority number
    plugin_seq = OrderPlugin(cfg_seq)
    plugin_perm = OrderPlugin(cfg_perm)

    ref_seq = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_seq))
    ref_perm = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_perm))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref_seq, ref_perm]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="seq3")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing
    # SEQUENTIAL runs first regardless of priority
    assert call_order == ["SeqFirst", "PermSecond"]

    await manager.shutdown()


@pytest.mark.asyncio
async def test_sequential_executes_before_concurrent():
    """SEQUENTIAL plugins run before CONCURRENT plugins."""

    call_order: list[str] = []

    class OrderPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            call_order.append(self.name)
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg_seq = make_plugin_config("SeqFirst2", PluginMode.SEQUENTIAL, priority=10)
    cfg_conc = make_plugin_config("ConcSecond", PluginMode.CONCURRENT, priority=1)
    plugin_seq = OrderPlugin(cfg_seq)
    plugin_conc = OrderPlugin(cfg_conc)

    ref_seq = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_seq))
    ref_conc = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_conc))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref_seq, ref_conc]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="seq4")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing
    # SEQUENTIAL runs first regardless of priority
    assert call_order == ["SeqFirst2", "ConcSecond"]

    await manager.shutdown()


# ---------------------------------------------------------------------------
# Phase ordering tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_phase_order_sequential_before_concurrent():
    """SEQUENTIAL phase completes before CONCURRENT phase begins."""

    phase_log: list[str] = []

    class SeqPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            phase_log.append("sequential")
            return PluginResult(continue_processing=True)

    class ConcPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            phase_log.append("concurrent")
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg_seq = make_plugin_config("PhaseSeq", PluginMode.SEQUENTIAL, priority=1)
    cfg_conc = make_plugin_config("PhaseConc", PluginMode.CONCURRENT, priority=1)
    plugin_seq = SeqPlugin(cfg_seq)
    plugin_conc = ConcPlugin(cfg_conc)

    ref_seq = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_seq))
    ref_conc = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_conc))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref_seq, ref_conc]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="phase1")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    assert result.continue_processing
    assert phase_log == ["sequential", "concurrent"]

    await manager.shutdown()


@pytest.mark.asyncio
async def test_fire_and_forget_fires_after_all_phases():
    """FIRE_AND_FORGET tasks are scheduled after SEQUENTIAL and CONCURRENT phases complete."""

    phase_log: list[str] = []
    fire_and_forget_started = asyncio.Event()

    class SeqPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            phase_log.append("sequential")
            return PluginResult(continue_processing=True)

    class FnfPlugin(Plugin):
        async def prompt_pre_fetch(self, payload, context):
            fire_and_forget_started.set()
            phase_log.append("fire_and_forget")
            return PluginResult(continue_processing=True)

    manager = await _make_manager()
    cfg_seq = make_plugin_config("FnfSeq", PluginMode.SEQUENTIAL, priority=1)
    cfg_fnf = make_plugin_config("FnfFnf", PluginMode.FIRE_AND_FORGET, priority=1)
    plugin_seq = SeqPlugin(cfg_seq)
    plugin_fnf = FnfPlugin(cfg_fnf)

    ref_seq = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_seq))
    ref_fnf = HookRef(PromptHookType.PROMPT_PRE_FETCH, PluginRef(plugin_fnf))

    with patch.object(manager._registry, "get_hook_refs_for_hook") as mock_get:
        mock_get.return_value = [ref_seq, ref_fnf]
        payload = PromptPrehookPayload(prompt_id="test", args={})
        global_context = GlobalContext(request_id="obs_order")

        result, _ = await manager.invoke_hook(PromptHookType.PROMPT_PRE_FETCH, payload, global_context)

    # Pipeline has returned; sequential ran synchronously
    assert "sequential" in phase_log
    # FIRE_AND_FORGET has not yet completed (fire-and-forget)
    assert not fire_and_forget_started.is_set()

    await asyncio.sleep(0.1)
    assert "fire_and_forget" in phase_log
    # FIRE_AND_FORGET always comes after sequential in the log
    assert phase_log.index("sequential") < phase_log.index("fire_and_forget")

    await manager.shutdown()
