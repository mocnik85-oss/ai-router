from __future__ import annotations

import unittest

from providers.openrouter import OpenRouterModel
from router.free_controller import FreeModelController, ModelRequirements


def make_model(
    model_id: str,
    *,
    context: int,
    free: bool = True,
    tools: bool = False,
    vision: bool = False,
) -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=context,
        prompt_price="0" if free else "0.000001",
        completion_price="0" if free else "0.000002",
        supports_tools=tools,
        supports_vision=vision,
        raw={},
    )


class FreeModelControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = FreeModelController()

    def test_filters_paid_models(self):
        models = [
            make_model("free", context=1000),
            make_model("paid", context=100000, free=False),
        ]

        candidates = self.controller.candidates(models)

        self.assertEqual([model.id for model in candidates], ["free"])

    def test_filters_for_tools(self):
        models = [
            make_model("no-tools", context=100000),
            make_model("tools", context=50000, tools=True),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(tools=True),
        )

        self.assertEqual([model.id for model in candidates], ["tools"])

    def test_filters_for_vision(self):
        models = [
            make_model("text-only", context=100000),
            make_model("vision", context=50000, vision=True),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(vision=True),
        )

        self.assertEqual([model.id for model in candidates], ["vision"])

    def test_filters_for_context(self):
        models = [
            make_model("small", context=50000),
            make_model("large", context=200000),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(min_context=100000),
        )

        self.assertEqual([model.id for model in candidates], ["large"])

    def test_select_returns_largest_context_candidate(self):
        models = [
            make_model("small", context=10000),
            make_model("large", context=200000),
            make_model("medium", context=50000),
        ]

        selected = self.controller.select(models)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, "large")

    def test_select_returns_none_when_no_candidate(self):
        models = [
            make_model("paid", context=200000, free=False),
        ]

        selected = self.controller.select(models)

        self.assertIsNone(selected)

    def test_empty_catalog(self):
        self.assertEqual(self.controller.candidates([]), [])
        self.assertIsNone(self.controller.select([]))


if __name__ == "__main__":
    unittest.main()
