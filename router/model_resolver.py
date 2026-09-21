"""Model resolution layer between OpenRouter and OpenCode namespaces.

OpenRouter models use ``org/model:variant`` IDs.  OpenCode expects
``opencode/<model-id>`` IDs.  This module translates between the two
while preserving the compatibility boundary.
"""

from __future__ import annotations

from dataclasses import replace

from providers.openrouter import OpenRouterModel


# Minimal mapping from OpenRouter catalogue IDs to routable backend
# (OpenCode) model IDs.  Intentionally small and explicit — models
# without a mapping are excluded with an explainable error.
DEFAULT_ROUTING_MAP: dict[str, str] = {
    "nvidia/nemotron-3.5-lightning:free": "opencode/nemotron-3.5-lightning-free",
}


class ModelResolver:
    """Translates OpenRouter catalogue IDs to routable backend model IDs.

    Preserves the compatibility boundary between model *discovery*
    (OpenRouter catalogue) and model *execution* (OpenCode backend).

    Models whose IDs already match a routable prefix (``opencode/``)
    pass through unchanged.  Other models are looked up in the
    explicit mapping; unmapped models are rejected.
    """

    ROUTABLE_PREFIX = "opencode/"

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self._mapping: dict[str, str] = (
            dict(mapping) if mapping is not None else dict(DEFAULT_ROUTING_MAP)
        )

    @property
    def routing_map(self) -> dict[str, str]:
        """Return a copy of the current routing map."""
        return dict(self._mapping)

    def resolves(self, model_id: str) -> bool:
        """Return *True* if *model_id* can be resolved to a routable ID."""
        return (
            model_id.startswith(self.ROUTABLE_PREFIX)
            or model_id in self._mapping
        )

    def resolve(self, model: OpenRouterModel) -> OpenRouterModel | None:
        """Return a copy of *model* with a routable backend ID, or ``None``."""
        if model.id.startswith(self.ROUTABLE_PREFIX):
            return model

        resolved_id = self._mapping.get(model.id)

        if resolved_id is None:
            return None

        return replace(model, id=resolved_id)

    def resolve_all(
        self, models: list[OpenRouterModel]
    ) -> list[OpenRouterModel]:
        """Resolve a list of models, keeping only routable ones."""
        resolved: list[OpenRouterModel] = []

        for model in models:
            result = self.resolve(model)

            if result is not None:
                resolved.append(result)

        return resolved
