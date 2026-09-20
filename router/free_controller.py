from __future__ import annotations

from dataclasses import dataclass

from providers.openrouter import OpenRouterModel


@dataclass(slots=True)
class ModelRequirements:
    """Capabilities required or preferred for a task."""

    tools: bool = False
    vision: bool = False
    reasoning: bool = False
    min_context: int = 0


@dataclass(slots=True)
class ScoredModel:
    """A free model together with its deterministic selection score."""

    model: OpenRouterModel
    score: int


class FreeModelController:
    """Select suitable zero-cost models from an OpenRouter catalog."""

    @staticmethod
    def _supports_reasoning(model: OpenRouterModel) -> bool:
        reasoning = model.raw.get("reasoning")
        return isinstance(reasoning, dict) and reasoning is not None

    @staticmethod
    def _is_router_model(model: OpenRouterModel) -> bool:
        return model.id == "openrouter/free"

    def score(
        self,
        model: OpenRouterModel,
        requirements: ModelRequirements | None = None,
    ) -> int:
        requirements = requirements or ModelRequirements()

        if not model.is_free:
            return -1

        if self._is_router_model(model):
            return -1

        if requirements.tools and not model.supports_tools:
            return -1

        if requirements.vision and not model.supports_vision:
            return -1

        context_length = model.context_length or 0

        if context_length < requirements.min_context:
            return -1

        score = 0

        # Required/preferred capabilities.
        if requirements.tools and model.supports_tools:
            score += 100

        if requirements.vision and model.supports_vision:
            score += 100

        if requirements.reasoning and self._supports_reasoning(model):
            score += 100

        # Prefer reasoning-capable models when reasoning is relevant.
        if self._supports_reasoning(model):
            score += 25

        # Context is useful, but deliberately has much less influence
        # than task capabilities.
        score += min(context_length // 100_000, 10)

        return score

    def ranked(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> list[ScoredModel]:
        scored: list[ScoredModel] = []

        for model in models:
            score = self.score(model, requirements)

            if score < 0:
                continue

            scored.append(
                ScoredModel(
                    model=model,
                    score=score,
                )
            )

        return sorted(
            scored,
            key=lambda item: (
                item.score,
                item.model.context_length or 0,
                item.model.id,
            ),
            reverse=True,
        )

    def candidates(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> list[OpenRouterModel]:
        return [
            item.model
            for item in self.ranked(models, requirements)
        ]

    def select(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> OpenRouterModel | None:
        candidates = self.candidates(models, requirements)

        if not candidates:
            return None

        return candidates[0]
