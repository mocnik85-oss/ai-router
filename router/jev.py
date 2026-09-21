"""JEV v0.1 — Deterministic orchestration layer.

Read-only execution only. No retries, no paid routing, no CLI/voice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Config
from providers.credentials import MemoryCredentialStore
from providers.openrouter import OpenRouterClient
from providers.opencode import OpenCodeClient, OpenCodeResult
from router.free_controller import FreeModelController, ModelRequirements
from router.model_resolver import ModelResolver
from router.policy import ExecutionPolicy, READ_ONLY


class JEVError(RuntimeError):
    """Raised when JEV encounters a policy or orchestration error."""


class PolicyViolationError(JEVError):
    """Raised when an execution policy enables disallowed capabilities."""


@dataclass(slots=True)
class JEVTask:
    """An inbound task for JEV to orchestrate."""

    prompt: str
    workdir: str
    requirements: ModelRequirements = field(default_factory=ModelRequirements)
    policy: ExecutionPolicy = field(default_factory=lambda: READ_ONLY)


@dataclass(slots=True)
class JEVResult:
    """Structured result returned by JEV after orchestration."""

    success: bool
    prompt: str
    model: str
    exit_code: int
    text: str
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class JEV:
    """v0.1 orchestrator: free model → ExecutionPolicy → OpenCode → result."""

    def __init__(
        self,
        *,
        client: OpenCodeClient | None = None,
        controller: FreeModelController | None = None,
        router_client: OpenRouterClient | None = None,
        resolver: ModelResolver | None = None,
    ) -> None:
        self._client = client or OpenCodeClient()
        self._controller = controller or FreeModelController()
        self._router_client = router_client or OpenRouterClient(
            Config(), MemoryCredentialStore()
        )
        self._resolver = resolver or ModelResolver()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        task: JEVTask,
        *,
        available_models: list[Any] | None = None,
    ) -> JEVResult:
        """Execute *task* and return a structured result.

        Parameters
        ----------
        task:
            The inbound task.
        available_models:
            Pre-fetched list of ``OpenRouterModel`` objects.  When *None*,
            JEV automatically fetches the catalogue from the injected
            ``OpenRouterClient`` (if one was provided at construction time)
            or falls back to an empty list.
        """

        self._validate_policy(task.policy)

        if available_models is not None:
            models = available_models
        elif self._router_client is not None:
            models = self._router_client.models()
        else:
            models = []

        discovered_count = len(models)

        # Resolve OpenRouter catalogue IDs to backend-routable IDs.
        # This is the explicit translation layer between model selection
        # (OpenRouter namespace) and execution (OpenCode namespace).
        models = self._resolver.resolve_all(models)

        resolved_count = len(models)

        # Defense-in-depth: also filter by backend capability.
        models = [
            m for m in models
            if self._client.can_route(m.id)
        ]

        selected_model = self._controller.select(
            models,
            task.requirements,
        )

        if selected_model is None:
            error = self._explain_no_model(
                discovered_count, resolved_count, len(models),
            )
            return JEVResult(
                success=False,
                prompt=task.prompt,
                model="",
                exit_code=-1,
                text="",
                error=error,
            )

        model_id = selected_model.id

        try:
            oc_result: OpenCodeResult = self._client.run(
                task.prompt,
                task.workdir,
                model=model_id,
            )
        except Exception as exc:
            return JEVResult(
                success=False,
                prompt=task.prompt,
                model=model_id,
                exit_code=-1,
                text="",
                error=str(exc),
            )

        return JEVResult(
            success=oc_result.success,
            prompt=task.prompt,
            model=model_id,
            exit_code=oc_result.exit_code,
            text=oc_result.text,
            events=[
                {"type": e.type, "raw": e.raw} for e in oc_result.events
            ],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _explain_no_model(
        discovered: int, resolved: int, routed: int,
    ) -> str:
        """Build an explainable error when no model can be executed."""

        if discovered > 0 and resolved == 0:
            return (
                "No suitable free model found. "
                f"All {discovered} discovered model(s) lack executable "
                "backend equivalents."
            )

        if discovered > resolved:
            unmapped = discovered - resolved
            return (
                "No suitable free model found. "
                f"{unmapped} of {discovered} discovered model(s) could not "
                "be mapped to a routable backend model."
            )

        return "No suitable free model found."

    @staticmethod
    def _validate_policy(policy: ExecutionPolicy) -> None:
        """Reject any policy that enables capabilities beyond read-only."""

        forbidden = {
            "edit": policy.edit,
            "shell": policy.shell,
            "network": policy.network,
            "git": policy.git,
            "commit": policy.commit,
        }

        enabled = [name for name, value in forbidden.items() if value]

        if enabled:
            raise PolicyViolationError(
                f"JEV v0.1 read-only mode rejects: {', '.join(enabled)}. "
                f"Only READ_ONLY is permitted."
            )
