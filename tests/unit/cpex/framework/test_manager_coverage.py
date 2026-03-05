# -*- coding: utf-8 -*-
"""Coverage tests for cpex.framework.manager — invoke_hook_for_plugin, _execute_with_timeout, audit mode."""

# Standard
import asyncio
from unittest.mock import MagicMock

# Third-Party
import pytest

# First-Party
from cpex.framework.base import HookRef, Plugin, PluginRef
from cpex.framework.errors import PluginError
from cpex.framework.manager import PluginExecutor, PluginManager
from cpex.framework.models import (
    GlobalContext,
    PluginConfig,
    PluginContext,
    PluginMode,
    PluginPayload,
    PluginResult,
    PluginViolation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(name="test", priority=100, mode=PluginMode.CONCURRENT, hooks=None):
    return PluginConfig(
        name=name,
        kind="test.Plugin",
        version="1.0",
        hooks=hooks or ["test_hook"],
        mode=mode,
        priority=priority,
    )


class ConcretePlugin(Plugin):
    async def test_hook(self, payload: PluginPayload, context: PluginContext) -> PluginResult:
        return PluginResult(continue_processing=True)


def _make_hook_ref(plugin=None, mode=PluginMode.CONCURRENT):
    plugin = plugin or ConcretePlugin(_make_config(mode=mode))
    ref = PluginRef(plugin)
    return HookRef("test_hook", ref)


# ===========================================================================
# invoke_hook_for_plugin
# ===========================================================================


class TestInvokeHookForPlugin:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        PluginManager.reset()
        yield
        PluginManager.reset()

    @pytest.mark.asyncio
    async def test_success(self):
        manager = PluginManager()
        manager._initialized = True
        hook_ref = _make_hook_ref()

        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = hook_ref

        payload = MagicMock(spec=PluginPayload)
        context = PluginContext(global_context=GlobalContext(request_id="1"))

        result = await manager.invoke_hook_for_plugin("test", "test_hook", payload, context)
        assert result.continue_processing is True

    @pytest.mark.asyncio
    async def test_not_found_raises(self):
        manager = PluginManager()
        manager._initialized = True
        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = None

        payload = MagicMock(spec=PluginPayload)
        context = PluginContext(global_context=GlobalContext(request_id="1"))

        with pytest.raises(PluginError, match="Unable to find"):
            await manager.invoke_hook_for_plugin("missing", "test_hook", payload, context)

    @pytest.mark.asyncio
    async def test_json_payload_dict(self):
        manager = PluginManager()
        manager._initialized = True

        plugin = ConcretePlugin(_make_config())
        plugin.json_to_payload = MagicMock(return_value=MagicMock(spec=PluginPayload))
        hook_ref = _make_hook_ref(plugin)

        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = hook_ref

        context = PluginContext(global_context=GlobalContext(request_id="1"))

        result = await manager.invoke_hook_for_plugin(
            "test", "test_hook", {"key": "val"}, context, payload_as_json=True
        )
        plugin.json_to_payload.assert_called_once_with("test_hook", {"key": "val"})
        assert result.continue_processing is True

    @pytest.mark.asyncio
    async def test_json_payload_wrong_type_raises(self):
        manager = PluginManager()
        manager._initialized = True

        hook_ref = _make_hook_ref()
        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = hook_ref

        context = PluginContext(global_context=GlobalContext(request_id="1"))

        with pytest.raises(ValueError, match="must be str or dict"):
            await manager.invoke_hook_for_plugin("test", "test_hook", 12345, context, payload_as_json=True)

    @pytest.mark.asyncio
    async def test_wrong_payload_type_raises(self):
        manager = PluginManager()
        manager._initialized = True

        hook_ref = _make_hook_ref()
        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = hook_ref

        context = PluginContext(global_context=GlobalContext(request_id="1"))

        with pytest.raises(ValueError, match="must be a PluginPayload"):
            await manager.invoke_hook_for_plugin("test", "test_hook", "not-a-payload", context, payload_as_json=False)

    @pytest.mark.asyncio
    async def test_global_context_auto_wrap(self):
        manager = PluginManager()
        manager._initialized = True
        hook_ref = _make_hook_ref()

        manager._registry = MagicMock()
        manager._registry.get_plugin_hook_by_name.return_value = hook_ref

        payload = MagicMock(spec=PluginPayload)
        global_context = GlobalContext(request_id="1")

        result = await manager.invoke_hook_for_plugin("test", "test_hook", payload, global_context)
        assert result.continue_processing is True


# ===========================================================================
# _execute_with_timeout observability
# ===========================================================================


class TestExecuteWithTimeout:
    @pytest.mark.asyncio
    async def test_with_trace_id(self):
        from cpex.framework.observability import current_trace_id

        mock_provider = MagicMock()
        mock_provider.start_span.return_value = "span-123"

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        hook_ref = _make_hook_ref()
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        token = current_trace_id.set("trace-abc")
        try:
            result = await executor._execute_with_timeout(hook_ref, payload, context)
        finally:
            current_trace_id.reset(token)

        assert result.continue_processing is True
        mock_provider.start_span.assert_called_once()
        mock_provider.end_span.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_trace(self):
        mock_provider = MagicMock()

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        hook_ref = _make_hook_ref()
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        # current_trace_id defaults to None, so no tracing should occur
        result = await executor._execute_with_timeout(hook_ref, payload, context)

        assert result.continue_processing is True
        mock_provider.start_span.assert_not_called()
        mock_provider.end_span.assert_not_called()

    @pytest.mark.asyncio
    async def test_observability_provider_failure(self):
        from cpex.framework.observability import current_trace_id

        mock_provider = MagicMock()
        mock_provider.start_span.side_effect = Exception("provider fail")

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        hook_ref = _make_hook_ref()
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        token = current_trace_id.set("trace-abc")
        try:
            result = await executor._execute_with_timeout(hook_ref, payload, context)
        finally:
            current_trace_id.reset(token)

        # Should still succeed despite provider failure
        assert result.continue_processing is True

    @pytest.mark.asyncio
    async def test_error_path_ends_span_with_error(self):
        """When plugin execution raises, end_span is called with status='error'."""
        from cpex.framework.observability import current_trace_id

        mock_provider = MagicMock()
        mock_provider.start_span.return_value = "span-err"

        class FailingPlugin(Plugin):
            async def test_hook(self, payload, context):
                raise RuntimeError("boom")

        plugin = FailingPlugin(_make_config())
        ref = PluginRef(plugin)
        hook_ref = HookRef("test_hook", ref)

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        token = current_trace_id.set("trace-err")
        try:
            with pytest.raises(RuntimeError, match="boom"):
                await executor._execute_with_timeout(hook_ref, payload, context)
        finally:
            current_trace_id.reset(token)

        mock_provider.start_span.assert_called_once()
        mock_provider.end_span.assert_called_once_with(span_id="span-err", status="error")

    @pytest.mark.asyncio
    async def test_error_path_end_span_also_fails(self):
        """When plugin raises AND end_span also raises, the original error propagates."""
        from cpex.framework.observability import current_trace_id

        mock_provider = MagicMock()
        mock_provider.start_span.return_value = "span-double-err"
        mock_provider.end_span.side_effect = Exception("end_span also broke")

        class FailingPlugin(Plugin):
            async def test_hook(self, payload, context):
                raise RuntimeError("plugin boom")

        plugin = FailingPlugin(_make_config())
        ref = PluginRef(plugin)
        hook_ref = HookRef("test_hook", ref)

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        token = current_trace_id.set("trace-double-err")
        try:
            with pytest.raises(RuntimeError, match="plugin boom"):
                await executor._execute_with_timeout(hook_ref, payload, context)
        finally:
            current_trace_id.reset(token)

        # end_span was attempted despite the error
        mock_provider.end_span.assert_called_once_with(span_id="span-double-err", status="error")

    @pytest.mark.asyncio
    async def test_end_span_failure_on_success_path(self):
        """When end_span raises after successful execution, the result is still returned."""
        from cpex.framework.observability import current_trace_id

        mock_provider = MagicMock()
        mock_provider.start_span.return_value = "span-ok"
        mock_provider.end_span.side_effect = Exception("end_span broke")

        executor = PluginExecutor(timeout=30, observability=mock_provider)
        hook_ref = _make_hook_ref()
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        token = current_trace_id.set("trace-ok")
        try:
            result = await executor._execute_with_timeout(hook_ref, payload, context)
        finally:
            current_trace_id.reset(token)

        # Plugin result is returned despite end_span failure
        assert result.continue_processing is True
        mock_provider.start_span.assert_called_once()
        mock_provider.end_span.assert_called_once()


# ===========================================================================
# Audit mode with no violation
# ===========================================================================


class TestAuditBlocking:
    @pytest.mark.asyncio
    async def test_audit_no_violation(self):
        """Plugin returns continue_processing=False in audit mode with no violation object."""
        plugin = ConcretePlugin(_make_config(mode=PluginMode.AUDIT))

        # Override to return blocking result with no violation
        async def blocking_hook(payload, context):
            return PluginResult(continue_processing=False, violation=None)

        ref = PluginRef(plugin)
        hook_ref = HookRef("test_hook", ref)
        hook_ref._func = blocking_hook

        executor = PluginExecutor(timeout=30)
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        result = await executor.execute_plugin(hook_ref, payload, context, False)
        # In audit mode, should still return the result (just log warning)
        assert result.continue_processing

    @pytest.mark.asyncio
    async def test_audit_with_violation_description(self):
        """Plugin returns violation with description in audit mode."""
        plugin = ConcretePlugin(_make_config(mode=PluginMode.AUDIT))

        async def blocking_hook(payload, context):
            return PluginResult(
                continue_processing=False,
                violation=PluginViolation(reason="test", description="detailed", code="V1"),
            )

        ref = PluginRef(plugin)
        hook_ref = HookRef("test_hook", ref)
        hook_ref._func = blocking_hook

        executor = PluginExecutor(timeout=30)
        context = PluginContext(global_context=GlobalContext(request_id="1"))
        payload = MagicMock(spec=PluginPayload)

        result = await executor.execute_plugin(hook_ref, payload, context, False)
        assert result.continue_processing
        assert not result.violation


# ===========================================================================
# Cross-type payload: unexpected type warning (manager.py line 251)
# ===========================================================================


class TestCrossTypeUnexpectedPayload:
    """When a plugin returns a modified_payload of an unexpected type (not
    PluginPayload or dict) under an explicit policy, the modification is
    silently ignored with a warning."""

    @pytest.mark.asyncio
    async def test_unexpected_type_ignored_with_policy(self):
        from cpex.framework.hooks.policies import HookPayloadPolicy

        class WeirdResultPlugin(Plugin):
            async def test_hook(self, payload, context):
                # Return an unexpected type (list) as modified_payload
                return PluginResult(continue_processing=True, modified_payload=["unexpected", "list"])

        config = _make_config(name="weird")
        plugin = WeirdResultPlugin(config)
        ref = PluginRef(plugin)
        hook_ref = HookRef("test_hook", ref)

        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset({"name"}))}
        executor = PluginExecutor(hook_policies=policies)

        payload = PluginPayload()
        global_ctx = GlobalContext(request_id="1")

        result, _ = await executor.execute(
            [hook_ref],
            payload,
            global_ctx,
            hook_type="test_hook",
        )
        # The unexpected type should be ignored — modified_payload stays None
        assert result.modified_payload is None


# ===========================================================================
# PluginManager Borg: hook_policies injection paths (lines 581-596)
# ===========================================================================


class TestBorgHookPoliciesInjection:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        PluginManager.reset()
        yield
        PluginManager.reset()

    def test_second_instantiation_injects_policies(self):
        """When the first PluginManager had no policies but a second one
        provides them, the policies are injected into the shared executor."""
        from cpex.framework.hooks.policies import HookPayloadPolicy

        # First instantiation — no policies
        pm1 = PluginManager()
        assert pm1._executor is not None
        assert not pm1._executor.hook_policies

        # Second instantiation — provides policies
        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset({"name"}))}
        pm2 = PluginManager(hook_policies=policies)

        # Borg pattern: both share state
        assert pm1._executor.hook_policies == policies
        assert pm2._executor.hook_policies == policies

    def test_second_instantiation_updates_timeout(self):
        """When the second instantiation provides a non-default timeout,
        it updates the shared executor's timeout."""
        from cpex.framework.hooks.policies import HookPayloadPolicy

        pm1 = PluginManager()
        _ = pm1._executor.timeout

        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset())}
        pm2 = PluginManager(timeout=120, hook_policies=policies)

        assert pm2._executor.timeout == 120

    def test_second_instantiation_warns_on_different_policies(self, caplog):
        """When policies are already set and a different set is provided,
        a warning is logged and the new policies are ignored."""
        from cpex.framework.hooks.policies import HookPayloadPolicy

        policies_a = {"hook_a": HookPayloadPolicy(writable_fields=frozenset({"x"}))}
        policies_b = {"hook_b": HookPayloadPolicy(writable_fields=frozenset({"y"}))}

        _ = PluginManager(hook_policies=policies_a)
        pm2 = PluginManager(hook_policies=policies_b)

        assert "already set" in caplog.text
        # Original policies are retained
        assert pm2._executor.hook_policies == policies_a

    def test_second_instantiation_injects_observability(self):
        """When observability is not yet set and a second instantiation
        provides it, the executor is updated."""
        from cpex.framework.hooks.policies import HookPayloadPolicy

        pm1 = PluginManager()
        assert pm1._executor.observability is None

        mock_obs = MagicMock()
        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset())}
        pm2 = PluginManager(hook_policies=policies, observability=mock_obs)

        assert pm2._executor.observability is mock_obs

    def test_defensive_executor_init_when_none(self):
        """When shared state exists but _executor is None (unusual test
        scenario), the defensive path creates a new PluginExecutor."""
        pm = PluginManager()
        # Simulate unusual state: shared state populated but executor nulled
        pm._executor = None

        # Re-instantiate without hook_policies — triggers defensive path
        pm2 = PluginManager()
        assert pm2._executor is not None
        assert isinstance(pm2._executor, PluginExecutor)


# ===========================================================================
# PluginManager executor property and setter (lines 605, 615, 620)
# ===========================================================================


class TestExecutorPropertySetter:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        PluginManager.reset()
        yield
        PluginManager.reset()

    def test_executor_property_returns_executor(self):
        pm = PluginManager()
        executor = pm.executor
        assert isinstance(executor, PluginExecutor)

    def test_executor_property_lazy_creates(self):
        """When _executor is None, the property lazily creates one."""
        pm = PluginManager()
        pm._executor = None
        executor = pm.executor
        assert isinstance(executor, PluginExecutor)

    def test_executor_setter(self):
        pm = PluginManager()
        new_executor = PluginExecutor()
        pm.executor = new_executor
        assert pm._executor is new_executor


# ===========================================================================
# PluginManager.shutdown lazy async_lock (line 810)
# ===========================================================================


class TestShutdownLazyAsyncLock:
    @pytest.fixture(autouse=True)
    def reset_manager(self):
        PluginManager.reset()
        yield
        PluginManager.reset()

    @pytest.mark.asyncio
    async def test_shutdown_creates_async_lock_lazily(self):
        """shutdown() should lazily create _async_lock if it is None."""
        pm = PluginManager()
        # Ensure _async_lock starts as None (fresh Borg state)
        assert pm._async_lock is None

        # shutdown on uninitialized manager should still create the lock
        await pm.shutdown()

        assert pm._async_lock is not None
        assert isinstance(pm._async_lock, asyncio.Lock)


# ===========================================================================
# Copy-on-Write payload isolation
# ===========================================================================


class TestCopyOnWritePayloadIsolation:
    """Verify that CoW-based isolation protects the live payload chain."""

    @pytest.fixture(autouse=True)
    def reset_manager(self):
        PluginManager.reset()
        yield
        PluginManager.reset()

    @pytest.mark.asyncio
    async def test_inplace_args_mutation_does_not_corrupt_chain(self):
        """Plugin that mutates payload.args[k] in-place should not affect
        the effective_payload seen by subsequent plugins."""
        from cpex.framework.hooks.policies import HookPayloadPolicy
        from cpex.framework.hooks.tools import ToolPreInvokePayload

        mutations_seen = []

        class MutatingPlugin(Plugin):
            async def test_hook(self, payload, context):
                # In-place mutation of the wrapped args dict
                payload.args["injected"] = "evil"
                mutations_seen.append(dict(payload.args))
                return PluginResult(continue_processing=True)

        class ObservingPlugin(Plugin):
            async def test_hook(self, payload, context):
                mutations_seen.append(dict(payload.args))
                return PluginResult(continue_processing=True)

        config_m = _make_config(name="mutator", priority=1)
        config_o = _make_config(name="observer", priority=2)
        plugin_m = MutatingPlugin(config_m)
        plugin_o = ObservingPlugin(config_o)

        hr_m = HookRef("test_hook", PluginRef(plugin_m))
        hr_o = HookRef("test_hook", PluginRef(plugin_o))

        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset({"args"}))}
        executor = PluginExecutor(hook_policies=policies)

        payload = ToolPreInvokePayload(name="calc", args={"x": "1"})
        ctx = GlobalContext(request_id="cow-test")

        await executor.execute([hr_m, hr_o], payload, ctx, hook_type="test_hook")

        # Mutator sees its own mutation
        assert mutations_seen[0] == {"x": "1", "injected": "evil"}
        # Observer should see the original args, not the mutation
        assert "injected" not in mutations_seen[1]
        assert mutations_seen[1] == {"x": "1"}

    @pytest.mark.asyncio
    async def test_http_header_setitem_goes_to_cow(self):
        """HttpHeaderPayload.__setitem__ writes go to CoW overlay,
        not the original header dict."""
        from cpex.framework.hooks.http import HttpHeaderPayload
        from cpex.framework.hooks.policies import HookPayloadPolicy

        original_headers = {"Authorization": "Bearer token"}

        class HeaderMutator(Plugin):
            async def test_hook(self, payload, context):
                payload.root["X-Injected"] = "bad"
                return PluginResult(continue_processing=True)

        config = _make_config(name="hdr_mutator", priority=1)
        plugin = HeaderMutator(config)
        hr = HookRef("test_hook", PluginRef(plugin))

        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset({"root"}))}
        executor = PluginExecutor(hook_policies=policies)

        payload = HttpHeaderPayload(root=original_headers)
        ctx = GlobalContext(request_id="hdr-cow-test")

        await executor.execute([hr], payload, ctx, hook_type="test_hook")

        # Original headers untouched
        assert "X-Injected" not in original_headers
        assert original_headers == {"Authorization": "Bearer token"}

    @pytest.mark.asyncio
    async def test_policy_filtering_works_with_cow(self):
        """Policy-based field filtering works correctly when the input
        was CoW-wrapped (plugin returns a new payload via model_copy)."""
        from cpex.framework.hooks.policies import HookPayloadPolicy
        from cpex.framework.hooks.tools import ToolPreInvokePayload

        class PolicyPlugin(Plugin):
            async def test_hook(self, payload, context):
                new = payload.model_copy(update={"name": "renamed"})
                return PluginResult(continue_processing=True, modified_payload=new)

        config = _make_config(name="policied")
        plugin = PolicyPlugin(config)
        hr = HookRef("test_hook", PluginRef(plugin))

        # Only "name" is writable
        policies = {"test_hook": HookPayloadPolicy(writable_fields=frozenset({"name"}))}
        executor = PluginExecutor(hook_policies=policies)

        payload = ToolPreInvokePayload(name="original", args={"a": "1"})
        ctx = GlobalContext(request_id="policy-cow-test")

        result, _ = await executor.execute([hr], payload, ctx, hook_type="test_hook")

        assert result.modified_payload is not None
        assert result.modified_payload.name == "renamed"
        assert result.modified_payload.args == {"a": "1"}

    @pytest.mark.asyncio
    async def test_deny_by_default_uses_cow_isolation(self):
        """When default_hook_policy=DENY, payload is CoW-isolated even
        without an explicit per-hook policy."""
        from cpex.framework.hooks.policies import DefaultHookPolicy
        from cpex.framework.hooks.tools import ToolPreInvokePayload
        from cpex.framework.memory import CopyOnWriteDict

        saw_cow = []

        class InspectPlugin(Plugin):
            async def test_hook(self, payload, context):
                saw_cow.append(isinstance(payload.args, CopyOnWriteDict))
                return PluginResult(continue_processing=True)

        config = _make_config(name="inspector")
        plugin = InspectPlugin(config)
        hr = HookRef("test_hook", PluginRef(plugin))

        executor = PluginExecutor()
        executor.default_hook_policy = DefaultHookPolicy.DENY

        payload = ToolPreInvokePayload(name="t", args={"k": "v"})
        ctx = GlobalContext(request_id="deny-cow-test")

        await executor.execute([hr], payload, ctx, hook_type="test_hook")

        assert saw_cow == [True]
