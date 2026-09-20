from __future__ import annotations

from dataclasses import dataclass

from providers.openrouter import OpenRouterModel


@dataclass(slots=True)
class ModelRequirements:
    """Capabilities required for a task."""

    tools: bool = False
    vision: bool = False
    min_context: int = 0


class FreeModelController:
    """Select suitable zero-cost models from an OpenRouter catalog."""

    def candidates(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> list[OpenRouterModel]:
        requirements = requirements or ModelRequirements()

        candidates: list[OpenRouterModel] = []

        for model in models:
            if not model.is_free:
                continue

            if requirements.tools and not model.supports_tools:
                continue

            if requirements.vision and not model.supports_vision:
                continue

            context_length = model.context_length or 0

            if context_length < requirements.min_context:
                continue

            candidates.append(model)

        return sorted(
            candidates,
            key=lambda model: (
                model.context_length or 0,
                model.supports_tools,
                model.supports_vision,
            ),
            reverse=True,
        )

    def select(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> OpenRouterModel | None:
        candidates = self.candidates(models, requirements)

        if not candidates:
            return None

        return candidates[0]
