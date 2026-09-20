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
    reasoning: bool = False,
) -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=context,
        prompt_price="0" if free else "0.000001",
        completion_price="0" if free else "0.000002",
        supports_tools=tools,
        supports_vision=vision,
        raw={
            "reasoning": {"supported_efforts": ["high"]}
            if reasoning
            else None
        },
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

    def test_excludes_openrouter_free_router(self):
        models = [
            make_model(
                "openrouter/free",
                context=1000000,
                tools=True,
                vision=True,
                reasoning=True,
            ),
            make_model(
                "real-model",
                context=100000,
                tools=True,
                reasoning=True,
            ),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(tools=True),
        )

        self.assertEqual([model.id for model in candidates], ["real-model"])

    def test_tools_requirement(self):
        models = [
            make_model("no-tools", context=100000),
            make_model("tools", context=50000, tools=True),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(tools=True),
        )

        self.assertEqual([model.id for model in candidates], ["tools"])

    def test_vision_requirement(self):
        models = [
            make_model("text-only", context=100000),
            make_model("vision", context=50000, vision=True),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(vision=True),
        )

        self.assertEqual([model.id for model in candidates], ["vision"])

    def test_reasoning_requirement(self):
        models = [
            make_model("plain", context=100000),
            make_model("reasoning", context=50000, reasoning=True),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(reasoning=True),
        )

        self.assertEqual(
            [model.id for model in candidates],
            ["reasoning", "plain"],
        )

    def test_context_requirement(self):
        models = [
            make_model("small", context=50000),
            make_model("large", context=200000),
        ]

        candidates = self.controller.candidates(
            models,
            ModelRequirements(min_context=100000),
        )

        self.assertEqual([model.id for model in candidates], ["large"])

    def test_capabilities_beat_context_size(self):
        models = [
            make_model(
                "huge-but-basic",
                context=1000000,
            ),
            make_model(
                "smaller-capable",
                context=100000,
                tools=True,
                reasoning=True,
            ),
        ]

        selected = self.controller.select(
            models,
            ModelRequirements(tools=True, reasoning=True),
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, "smaller-capable")

    def test_ranked_returns_scores(self):
        models = [
            make_model(
                "basic",
                context=100000,
                tools=True,
            ),
            make_model(
                "capable",
                context=100000,
                tools=True,
                reasoning=True,
            ),
        ]

        ranked = self.controller.ranked(
            models,
            ModelRequirements(tools=True),
        )

        self.assertEqual(ranked[0].model.id, "capable")
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_select_returns_none_when_no_candidate(self):
        models = [
            make_model("paid", context=200000, free=False),
        ]

        self.assertIsNone(self.controller.select(models))

    def test_empty_catalog(self):
        self.assertEqual(self.controller.candidates([]), [])
        self.assertEqual(self.controller.ranked([]), [])
        self.assertIsNone(self.controller.select([]))


if __name__ == "__main__":
    unittest.main()
