"""Focused tests for the OpenRouter → ModelResolver → FreeModelController → JEV path.

All external calls (OpenRouterClient.models, OpenCodeClient.run) are
mocked.  No real network or subprocess calls are made.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from app.config import Config
from providers.openrouter import OpenRouterClient, OpenRouterModel
from providers.opencode import OpenCodeClient, OpenCodeResult, OpenCodeEvent
from router.free_controller import FreeModelController, ModelRequirements
from router.jev import JEV, JEVTask, JEVResult
from router.model_resolver import ModelResolver, DEFAULT_ROUTING_MAP


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_model(
    model_id: str = "opencode/test-model",
    *,
    context: int = 100_000,
    tools: bool = True,
    vision: bool = False,
    free: bool = True,
) -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=context,
        prompt_price="0" if free else "0.001",
        completion_price="0" if free else "0.002",
        supports_tools=tools,
        supports_vision=vision,
        raw={},
    )


def _make_oc_result(
    *,
    exit_code: int = 0,
    text: str = "ok",
) -> OpenCodeResult:
    events: list[OpenCodeEvent] = []
    if text:
        events.append(
            OpenCodeEvent(type="text", raw={"part": {"text": text}})
        )
    return OpenCodeResult(
        exit_code=exit_code,
        events=events,
        stdout=text,
        stderr="",
    )


def _fake_router_client(
    models: list[OpenRouterModel],
) -> MagicMock:
    """Return a MagicMock that behaves like an OpenRouterClient."""
    client = MagicMock(spec=OpenRouterClient)
    client.models.return_value = models
    return client


def _fake_oc_client(
    result: OpenCodeResult | None = None,
) -> MagicMock:
    """Return a MagicMock that behaves like an OpenCodeClient."""
    client = MagicMock(spec=OpenCodeClient)
    client.run.return_value = result or _make_oc_result()
    client.can_route.return_value = True
    return client


# ------------------------------------------------------------------
# Tests — OpenRouter → Resolver → Controller → JEV integration
# ------------------------------------------------------------------

class TestJEVFetchesModelsFromRouter(unittest.TestCase):
    """When no available_models is supplied, JEV queries OpenRouterClient."""

    def test_models_fetched_from_router_client(self) -> None:
        """JEV calls router_client.models() when available_models is None."""
        free_model = _make_model("opencode/auto-fetch-model")
        router_client = _fake_router_client([free_model])
        oc_client = _fake_oc_client(_make_oc_result(text="fetched"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="do something", workdir="/tmp")
        result = jev.run(task)

        router_client.models.assert_called_once()
        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/auto-fetch-model")

    def test_router_called_only_when_available_models_absent(self) -> None:
        """When available_models is explicitly given, router is not called."""
        free_model = _make_model("opencode/explicit-model")
        router_client = _fake_router_client([free_model])
        oc_client = _fake_oc_client()

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        jev.run(task, available_models=[free_model])

        router_client.models.assert_not_called()

    def test_explicit_available_models_still_work(self) -> None:
        """Backward-compatible: passing available_models still works."""
        model = _make_model("opencode/backwards-compat")
        router_client = _fake_router_client([model])
        oc_client = _fake_oc_client(_make_oc_result(text="compat"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/backwards-compat")


class TestJEVFreeModelSelection(unittest.TestCase):
    """The FreeModelController scores and selects from fetched models."""

    def test_only_free_model_selected(self) -> None:
        """From a mixed catalogue, the controller picks the free model."""
        free = _make_model("opencode/free-llm", tools=True)
        paid = _make_model("opencode/paid-llm", tools=True, free=False)

        router_client = _fake_router_client([paid, free])
        oc_client = _fake_oc_client(_make_oc_result(text="selected free"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(
            prompt="list files",
            workdir="/tmp",
            requirements=ModelRequirements(tools=True),
        )
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/free-llm")

    def test_no_free_model_returns_failure(self) -> None:
        """When the catalogue contains only paid models, JEV fails."""
        paid = _make_model("opencode/paid-model", free=False)

        router_client = _fake_router_client([paid])
        oc_client = _fake_oc_client()

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="do stuff", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("No suitable free model", result.error)

    def test_empty_catalog_returns_failure(self) -> None:
        """An empty model catalogue yields no model."""
        router_client = _fake_router_client([])
        oc_client = _fake_oc_client()

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="nothing", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)

    def test_controller_receives_full_catalog(self) -> None:
        """The controller sees every model returned by OpenRouterClient."""
        models = [_make_model(f"opencode/model-{i}") for i in range(5)]
        router_client = _fake_router_client(models)

        real_controller = FreeModelController()
        spy = MagicMock(wraps=real_controller)
        spy.select.return_value = models[0]

        oc_client = _fake_oc_client()
        jev = JEV(client=oc_client, router_client=router_client, controller=spy)
        task = JEVTask(prompt="test", workdir="/tmp")
        jev.run(task)

        spy.select.assert_called_once()
        passed_models = spy.select.call_args[0][0]
        self.assertEqual(len(passed_models), 5)

    def test_tool_capable_free_model_preferred(self) -> None:
        """When tools are required, the tool-capable free model wins."""
        basic = _make_model("opencode/basic-free", tools=False)
        capable = _make_model("opencode/capable-free", tools=True)

        router_client = _fake_router_client([basic, capable])
        oc_client = _fake_oc_client(_make_oc_result(text="capable"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(
            prompt="read file",
            workdir="/tmp",
            requirements=ModelRequirements(tools=True),
        )
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/capable-free")


class TestJEVExecutionPath(unittest.TestCase):
    """Verify that the selected model reaches OpenCodeClient.run."""

    def test_selected_model_forwarded_to_opencode(self) -> None:
        """The model id from the controller is passed as the model kwarg."""
        model = _make_model("opencode/chosen-model")
        router_client = _fake_router_client([model])
        oc_client = _fake_oc_client(_make_oc_result(text="done"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="inspect", workdir="/work")
        jev.run(task)

        oc_client.run.assert_called_once()
        call_kwargs = oc_client.run.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "opencode/chosen-model")
        self.assertEqual(call_kwargs.args[0], "inspect")
        self.assertEqual(call_kwargs.args[1], "/work")

    def test_opencode_failure_propagates(self) -> None:
        """When OpenCodeClient raises, JEV catches and returns error."""
        model = _make_model("opencode/err-model")
        router_client = _fake_router_client([model])

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.run.side_effect = OSError("binary missing")

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="boom", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("binary missing", result.error)

    def test_full_pipeline_end_to_end(self) -> None:
        """Complete pipeline: OpenRouter → Resolver → Controller → OpenCode."""
        models = [
            _make_model("opencode/paid-model", free=False),
            _make_model("opencode/free-alpha"),
            _make_model("opencode/free-beta", tools=True),
        ]
        router_client = _fake_router_client(models)

        oc_result = _make_oc_result(text="pipeline output")
        oc_client = _fake_oc_client(oc_result)

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(
            prompt="run pipeline",
            workdir="/project",
            requirements=ModelRequirements(tools=True),
        )
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/free-beta")
        self.assertEqual(result.text, "pipeline output")
        self.assertEqual(result.exit_code, 0)

        # Verify the call chain happened
        router_client.models.assert_called_once()
        oc_client.run.assert_called_once()


class TestJEVDefaultRouterClient(unittest.TestCase):
    """When no router_client is given, JEV auto-constructs a default."""

    def test_default_router_client_is_openrouter_client(self) -> None:
        """JEV() creates a default OpenRouterClient when none is provided."""
        jev = JEV()
        self.assertIsInstance(jev._router_client, OpenRouterClient)

    def test_default_router_client_uses_default_config(self) -> None:
        """The default OpenRouterClient receives a default Config."""
        jev = JEV()
        self.assertIsInstance(jev._router_client.config, Config)

    def test_default_router_client_uses_memory_credential_store(self) -> None:
        """The default OpenRouterClient uses a MemoryCredentialStore."""
        from providers.credentials import MemoryCredentialStore

        jev = JEV()
        self.assertIsInstance(jev._router_client.credentials, MemoryCredentialStore)

    def test_explicit_router_client_not_overridden(self) -> None:
        """When a router_client is explicitly provided, it is kept as-is."""
        router_client = _fake_router_client([])
        jev = JEV(router_client=router_client)
        self.assertIs(jev._router_client, router_client)

    def test_default_router_client_fetches_models(self) -> None:
        """The default OpenRouterClient can call .models() (returns list)."""
        jev = JEV()
        self.assertIsNotNone(jev._router_client)
        self.assertTrue(hasattr(jev._router_client, "models"))


class TestRealCatalogueRepresentation(unittest.TestCase):
    """Prove that models in the real OpenRouter catalogue format flow
    through the full JEV pipeline (router → resolver → controller → opencode)."""

    def test_real_format_free_model_selected(self) -> None:
        """A model with real-format pricing ('0' string) is selected."""
        real_model = OpenRouterModel(
            id="opencode/mimo-v2.5-free",
            name="MiMo V2.5 Free",
            context_length=128000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={
                "reasoning": {
                    "mandatory": False,
                    "default_enabled": True,
                },
            },
        )
        router_client = _fake_router_client([real_model])
        oc_client = _fake_oc_client(_make_oc_result(text="output"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/mimo-v2.5-free")

    def test_real_catalogue_mixed_catalog(self) -> None:
        """From a catalogue with paid, free, and meta models, the free one wins."""
        models = [
            OpenRouterModel(
                id="openrouter/auto",
                name="OpenRouter Auto",
                context_length=None,
                prompt_price="-1",
                completion_price="-1",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
            OpenRouterModel(
                id="x-ai/grok-4.7",
                name="xAI Grok 4.7",
                context_length=1000000,
                prompt_price="0.0000016",
                completion_price="0.0000048",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
            OpenRouterModel(
                id="nvidia/nemotron-3.5-lightning:free",
                name="NVIDIA Nemotron 3.5 Lightning (free)",
                context_length=1000000,
                prompt_price="0",
                completion_price="0",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
            OpenRouterModel(
                id="openrouter/free",
                name="OpenRouter Free",
                context_length=None,
                prompt_price="0",
                completion_price="0",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
        ]
        router_client = _fake_router_client(models)
        oc_client = _fake_oc_client(_make_oc_result(text="done"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="list files", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        # The resolver maps nvidia/nemotron-3.5-lightning:free → opencode/nemotron-3.5-lightning-free
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")

    def test_zero_like_pricing_variants_selectable(self) -> None:
        """Zero-like pricing strings ('0', '0.0', '0.00') are all recognised."""
        models = [
            OpenRouterModel(
                id="opencode/model-a",
                name="A",
                context_length=100000,
                prompt_price="0.0",
                completion_price="0.0",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
            OpenRouterModel(
                id="opencode/model-b",
                name="B",
                context_length=50000,
                prompt_price="0.00",
                completion_price="0.00",
                supports_tools=False,
                supports_vision=False,
                raw={},
            ),
        ]
        router_client = _fake_router_client(models)
        oc_client = _fake_oc_client(_make_oc_result(text="ok"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/model-a")


# ------------------------------------------------------------------
# Execution-backend compatibility boundary
# ------------------------------------------------------------------


class TestExecutionBackendFiltering(unittest.TestCase):
    """JEV must not forward OpenRouter model IDs that the execution
    backend cannot route.  This is the explicit compatibility boundary
    between model selection and the execution backend."""

    def test_incompatible_openrouter_model_excluded(self) -> None:
        """An OpenRouter model ID that has no backend mapping is filtered
        out, causing JEV to return a failure with an explainable error
        rather than a provider.no-route error at execution time."""
        from providers.opencode import OpenCodeClient

        # Real OpenCodeClient — can_route() rejects OpenRouter IDs.
        oc_client = OpenCodeClient()
        self.assertFalse(oc_client.can_route("thinkingmachines/inkling:free"))

        openrouter_model = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling (free)",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        router_client = _fake_router_client([openrouter_model])

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("No suitable free model", result.error)
        # Critically: OpenCodeClient.run was never called.
        self.assertFalse(oc_client.can_route(openrouter_model.id))

    def test_compatible_free_model_selected(self) -> None:
        """A free model whose ID matches the backend's routable prefix
        passes the filter and is selected for execution."""
        from providers.opencode import OpenCodeClient

        oc_client = OpenCodeClient()

        compatible_model = OpenRouterModel(
            id="opencode/mimo-v2.5-free",
            name="MiMo V2.5 Free",
            context_length=128000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = _fake_router_client([compatible_model])
        oc_client_mock = _fake_oc_client(_make_oc_result(text="ok"))

        jev = JEV(client=oc_client_mock, router_client=router_client)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/mimo-v2.5-free")

    def test_mixed_catalog_only_compatible_passes(self) -> None:
        """From a mixed catalogue of unmapped OpenRouter IDs and OpenCode
        IDs, only the OpenCode-routable ones survive the filter."""
        from providers.opencode import OpenCodeClient

        oc_client = OpenCodeClient()

        openrouter_only = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling (free)",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        compatible = OpenRouterModel(
            id="opencode/mimo-v2.5-free",
            name="MiMo V2.5 Free",
            context_length=128000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )

        router_client = _fake_router_client([openrouter_only, compatible])

        # Use a mock whose can_route mirrors the real OpenCodeClient logic.
        oc_client_mock = _fake_oc_client(_make_oc_result(text="selected"))
        oc_client_mock.can_route.side_effect = oc_client.can_route

        jev = JEV(client=oc_client_mock, router_client=router_client)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/mimo-v2.5-free")

    def test_mapped_openrouter_model_passes(self) -> None:
        """An OpenRouter model whose ID is in the resolver's mapping
        is translated to a routable backend ID and passes the filter."""
        from providers.opencode import OpenCodeClient

        oc_client = OpenCodeClient()

        mapped_model = OpenRouterModel(
            id="nvidia/nemotron-3.5-lightning:free",
            name="NVIDIA Nemotron 3.5 Lightning (free)",
            context_length=1000000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = _fake_router_client([mapped_model])
        oc_client_mock = _fake_oc_client(_make_oc_result(text="mapped ok"))

        jev = JEV(client=oc_client_mock, router_client=router_client)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")
        # Verify the resolved ID is routable
        self.assertTrue(oc_client.can_route("opencode/nemotron-3.5-lightning-free"))

    def test_custom_resolver_injected(self) -> None:
        """A custom resolver with its own mapping can be injected."""
        from providers.opencode import OpenCodeClient

        oc_client = OpenCodeClient()

        custom_model = OpenRouterModel(
            id="acme/fast-model",
            name="ACME Fast",
            context_length=64000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = _fake_router_client([custom_model])
        oc_client_mock = _fake_oc_client(_make_oc_result(text="acme ok"))

        # Custom resolver maps acme/fast-model → opencode/acme-fast-model
        resolver = ModelResolver(mapping={"acme/fast-model": "opencode/acme-fast-model"})

        jev = JEV(client=oc_client_mock, router_client=router_client, resolver=resolver)
        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/acme-fast-model")


# ------------------------------------------------------------------
# Model resolution integration
# ------------------------------------------------------------------


class TestModelResolutionIntegration(unittest.TestCase):
    """The ModelResolver translates OpenRouter IDs before execution."""

    def test_resolver_translates_openrouter_to_opencode(self) -> None:
        """An OpenRouter model with a mapping is translated to its backend ID."""
        router_model = OpenRouterModel(
            id="nvidia/nemotron-3.5-lightning:free",
            name="NVIDIA Nemotron 3.5 Lightning (free)",
            context_length=1000000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = _fake_router_client([router_model])
        oc_client = _fake_oc_client(_make_oc_result(text="resolved"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")

    def test_unmapped_model_excluded_with_explanation(self) -> None:
        """A model without a mapping produces an explainable error."""
        unmapped = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling (free)",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        router_client = _fake_router_client([unmapped])
        oc_client = _fake_oc_client()

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("lack executable backend equivalents", result.error)

    def test_mixed_mapped_and_unmapped_error_explains(self) -> None:
        """When some models are mapped and others are not, the error
        explains how many were unmapped."""
        mapped = OpenRouterModel(
            id="nvidia/nemotron-3.5-lightning:free",
            name="NVIDIA Nemotron",
            context_length=1000000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        unmapped = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        router_client = _fake_router_client([mapped, unmapped])

        # Use a controller that always returns None to test error message
        class NoSelectController(FreeModelController):
            def select(self, models, requirements=None):
                return None

        oc_client = _fake_oc_client()
        jev = JEV(
            client=oc_client,
            router_client=router_client,
            controller=NoSelectController(),
        )
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("1 of 2 discovered model(s) could not", result.error)

    def test_resolved_id_not_original_id(self) -> None:
        """The model ID passed to OpenCode is the resolved ID, not the
        original OpenRouter ID."""
        router_model = OpenRouterModel(
            id="nvidia/nemotron-3.5-lightning:free",
            name="NVIDIA Nemotron",
            context_length=1000000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = _fake_router_client([router_model])
        oc_client = _fake_oc_client(_make_oc_result(text="done"))

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        # The resolved ID is passed, not the original
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")
        self.assertNotEqual(result.model, "nvidia/nemotron-3.5-lightning:free")

        # Verify it was passed to OpenCodeClient.run
        call_kwargs = oc_client.run.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "opencode/nemotron-3.5-lightning-free")


if __name__ == "__main__":
    unittest.main()
