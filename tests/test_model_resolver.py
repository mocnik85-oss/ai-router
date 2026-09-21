"""Focused unit tests for router.model_resolver — ModelResolver.

Tests cover:
  a) routable model resolution
  b) non-routable model rejection
  c) JEV selecting an actually executable free model
  d) the real model ID passed to OpenCode
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from providers.openrouter import OpenRouterModel
from providers.opencode import OpenCodeClient, OpenCodeResult, OpenCodeEvent
from router.model_resolver import ModelResolver, DEFAULT_ROUTING_MAP
from router.jev import JEV, JEVTask


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_model(
    model_id: str,
    *,
    context: int = 100_000,
    tools: bool = True,
    free: bool = True,
) -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=context,
        prompt_price="0" if free else "0.001",
        completion_price="0" if free else "0.002",
        supports_tools=tools,
        supports_vision=False,
        raw={},
    )


def _make_oc_result(text: str = "ok") -> OpenCodeResult:
    events: list[OpenCodeEvent] = []
    if text:
        events.append(
            OpenCodeEvent(type="text", raw={"part": {"text": text}})
        )
    return OpenCodeResult(
        exit_code=0,
        events=events,
        stdout=text,
        stderr="",
    )


# ------------------------------------------------------------------
# a) Routable model resolution
# ------------------------------------------------------------------


class TestRoutableModelResolution(unittest.TestCase):
    """Models with mappings or opencode/ prefix are resolved to routable IDs."""

    def test_opencode_prefixed_model_passes_through(self) -> None:
        """A model whose ID already starts with opencode/ is returned as-is."""
        resolver = ModelResolver(mapping={})
        model = _make_model("opencode/mimo-v2.5-free")

        resolved = resolver.resolve(model)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, "opencode/mimo-v2.5-free")
        self.assertIs(resolved, model)  # same object, no copy needed

    def test_mapped_openrouter_model_resolved(self) -> None:
        """An OpenRouter model with a mapping is resolved to its backend ID."""
        resolver = ModelResolver(mapping={
            "nvidia/nemotron-3.5-lightning:free": "opencode/nemotron-3.5-lightning-free",
        })
        model = _make_model("nvidia/nemotron-3.5-lightning:free")

        resolved = resolver.resolve(model)

        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.id, "opencode/nemotron-3.5-lightning-free")
        # Other fields preserved
        self.assertEqual(resolved.name, model.name)
        self.assertEqual(resolved.context_length, model.context_length)
        self.assertEqual(resolved.prompt_price, model.prompt_price)
        self.assertTrue(resolved.supports_tools)

    def test_resolve_all_keeps_routable_models(self) -> None:
        """resolve_all returns only models that could be resolved."""
        resolver = ModelResolver(mapping={
            "nvidia/nemotron-3.5-lightning:free": "opencode/nemotron-3.5-lightning-free",
        })
        models = [
            _make_model("opencode/mimo-v2.5-free"),
            _make_model("nvidia/nemotron-3.5-lightning:free"),
            _make_model("thinkingmachines/inkling:free"),  # no mapping
        ]

        resolved = resolver.resolve_all(models)

        self.assertEqual(len(resolved), 2)
        ids = [m.id for m in resolved]
        self.assertIn("opencode/mimo-v2.5-free", ids)
        self.assertIn("opencode/nemotron-3.5-lightning-free", ids)
        self.assertNotIn("thinkingmachines/inkling:free", ids)

    def test_resolves_returns_true_for_mapped_ids(self) -> None:
        """resolves() returns True for both prefix-matched and mapped IDs."""
        resolver = ModelResolver(mapping={
            "nvidia/nemotron-3.5-lightning:free": "opencode/nemotron-3.5-lightning-free",
        })

        self.assertTrue(resolver.resolves("opencode/mimo-v2.5-free"))
        self.assertTrue(resolver.resolves("nvidia/nemotron-3.5-lightning:free"))
        self.assertFalse(resolver.resolves("thinkingmachines/inkling:free"))

    def test_default_routing_map_is_used(self) -> None:
        """When no custom mapping is provided, the default map is used."""
        resolver = ModelResolver()

        self.assertEqual(
            resolver.routing_map,
            DEFAULT_ROUTING_MAP,
        )
        self.assertIn(
            "nvidia/nemotron-3.5-lightning:free",
            resolver.routing_map,
        )

    def test_custom_mapping_replaces_default(self) -> None:
        """A custom mapping completely replaces the default."""
        custom = {"acme/model:free": "opencode/acme-model-free"}
        resolver = ModelResolver(mapping=custom)

        self.assertEqual(resolver.routing_map, custom)
        # Default mapping is NOT present
        self.assertNotIn(
            "nvidia/nemotron-3.5-lightning:free",
            resolver.routing_map,
        )

    def test_empty_mapping_rejects_all_non_prefixed(self) -> None:
        """An empty mapping rejects all non-prefixed models."""
        resolver = ModelResolver(mapping={})

        model = _make_model("nvidia/nemotron-3.5-lightning:free")
        resolved = resolver.resolve(model)

        self.assertIsNone(resolved)


# ------------------------------------------------------------------
# b) Non-routable model rejection
# ------------------------------------------------------------------


class TestNonRoutableModelRejection(unittest.TestCase):
    """Models without mappings or correct prefixes are rejected."""

    def test_unmapped_openrouter_model_returns_none(self) -> None:
        """An OpenRouter model ID without a mapping returns None."""
        resolver = ModelResolver(mapping={})
        model = _make_model("thinkingmachines/inkling:free")

        resolved = resolver.resolve(model)

        self.assertIsNone(resolved)

    def test_resolve_all_filters_out_unmapped(self) -> None:
        """resolve_all excludes models that cannot be resolved."""
        resolver = ModelResolver(mapping={})
        models = [
            _make_model("thinkingmachines/inkling:free"),
            _make_model("google/gemma-4-26b-a4b-it:free"),
            _make_model("meta-llama/llama-4-maverick:free"),
        ]

        resolved = resolver.resolve_all(models)

        self.assertEqual(resolved, [])

    def test_partially_mapped_list(self) -> None:
        """When some models are mapped and others are not, only mapped survive."""
        resolver = ModelResolver(mapping={
            "nvidia/nemotron-3.5-lightning:free": "opencode/nemotron-3.5-lightning-free",
        })
        models = [
            _make_model("nvidia/nemotron-3.5-lightning:free"),
            _make_model("thinkingmachines/inkling:free"),
        ]

        resolved = resolver.resolve_all(models)

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0].id, "opencode/nemotron-3.5-lightning-free")

    def test_empty_model_list(self) -> None:
        """An empty model list returns an empty resolved list."""
        resolver = ModelResolver()
        self.assertEqual(resolver.resolve_all([]), [])


# ------------------------------------------------------------------
# c) JEV selecting an actually executable free model
# ------------------------------------------------------------------


class TestJEVSelectsExecutableFreeModel(unittest.TestCase):
    """JEV integrates with ModelResolver to select models that can
    actually be executed by the backend."""

    def test_jev_resolves_and_selects(self) -> None:
        """JEV resolves OpenRouter models and selects an executable one."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

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
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = [router_model]

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.run.return_value = _make_oc_result(text="resolved ok")
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test resolution", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")

    def test_jev_rejects_unmapped_with_explanation(self) -> None:
        """JEV rejects unmapped models and returns an explainable error."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

        unmapped_model = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling (free)",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = [unmapped_model]

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test unmapped", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        self.assertIn("lack executable backend equivalents", result.error)
        # run() was never called
        oc_client.run.assert_not_called()

    def test_jev_prefers_highest_scoring_resolved_model(self) -> None:
        """When multiple models resolve, the FreeModelController picks the best."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

        models = [
            OpenRouterModel(
                id="nvidia/nemotron-3.5-lightning:free",
                name="NVIDIA Nemotron",
                context_length=1000000,
                prompt_price="0",
                completion_price="0",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
            OpenRouterModel(
                id="opencode/mimo-v2.5-free",
                name="MiMo V2.5 Free",
                context_length=128000,
                prompt_price="0",
                completion_price="0",
                supports_tools=True,
                supports_vision=False,
                raw={},
            ),
        ]
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = models

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.run.return_value = _make_oc_result(text="selected")
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="test selection", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        # Nemotron has higher context → higher score → wins
        self.assertEqual(result.model, "opencode/nemotron-3.5-lightning-free")


# ------------------------------------------------------------------
# d) The real model ID passed to OpenCode
# ------------------------------------------------------------------


class TestRealModelIDPassedToOpenCode(unittest.TestCase):
    """The model ID forwarded to OpenCodeClient.run is the resolved ID,
    never the original OpenRouter catalogue ID."""

    def test_resolved_id_forwarded_not_original(self) -> None:
        """OpenCodeClient.run receives the resolved opencode/ ID."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

        original_id = "nvidia/nemotron-3.5-lightning:free"
        expected_id = "opencode/nemotron-3.5-lightning-free"

        model = OpenRouterModel(
            id=original_id,
            name="NVIDIA Nemotron",
            context_length=1000000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = [model]

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.run.return_value = _make_oc_result(text="ok")
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="verify ID", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, expected_id)

        # Verify the exact model kwarg passed to OpenCodeClient.run
        call_kwargs = oc_client.run.call_args
        self.assertEqual(call_kwargs.kwargs["model"], expected_id)

    def test_opencode_prefixed_id_unchanged(self) -> None:
        """A model with opencode/ prefix is forwarded unchanged."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

        model = OpenRouterModel(
            id="opencode/mimo-v2.5-free",
            name="MiMo V2.5 Free",
            context_length=128000,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=False,
            raw={},
        )
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = [model]

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.run.return_value = _make_oc_result(text="ok")
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="verify passthrough", workdir="/tmp")
        result = jev.run(task)

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/mimo-v2.5-free")

        call_kwargs = oc_client.run.call_args
        self.assertEqual(call_kwargs.kwargs["model"], "opencode/mimo-v2.5-free")

    def test_unmapped_model_never_reaches_opencode(self) -> None:
        """An unmapped model is rejected before reaching OpenCodeClient.run."""
        from unittest.mock import MagicMock
        from providers.openrouter import OpenRouterClient

        model = OpenRouterModel(
            id="thinkingmachines/inkling:free",
            name="Thinking Machines: Inkling (free)",
            context_length=1048576,
            prompt_price="0",
            completion_price="0",
            supports_tools=True,
            supports_vision=True,
            raw={},
        )
        router_client = MagicMock(spec=OpenRouterClient)
        router_client.models.return_value = [model]

        oc_client = MagicMock(spec=OpenCodeClient)
        oc_client.can_route.return_value = True

        jev = JEV(client=oc_client, router_client=router_client)
        task = JEVTask(prompt="should not run", workdir="/tmp")
        result = jev.run(task)

        self.assertFalse(result.success)
        oc_client.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
