"""OpenCode execution adapter — the provider execution boundary.

``OpenCodeClient.run()`` is the point where a governed execution
request becomes a concrete OpenCode process.  The governing
:class:`router.policy.ExecutionPolicy` is therefore enforced *here*,
immediately before the process is spawned, and not only by the
orchestrator's upstream validation.

Protocol V1.1 boundary: the provider returns raw execution
information only (:class:`OpenCodeResult` — exit code, events,
stdout/stderr).  It imports no ``protocol`` governance model, holds no
authoritative task/project artifacts, and therefore cannot transition
authoritative state; interpreting its output belongs to the PM
interpretation layer (``protocol.interpretation``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
from typing import Any

from router.policy import READ_ONLY, ExecutionPolicy, write_capabilities


class OpenCodeError(RuntimeError):
    """Base exception for OpenCode execution errors."""


class OpenCodeTimeoutError(OpenCodeError):
    """Raised when OpenCode exceeds its execution timeout."""


class OpenCodePolicyError(OpenCodeError):
    """Raised at the execution boundary when the governing policy forbids
    the execution (e.g. a write-capable policy under read-only governance)."""


@dataclass(slots=True)
class OpenCodeEvent:
    """One JSON event emitted by OpenCode."""

    type: str
    raw: dict[str, Any]


@dataclass(slots=True)
class OpenCodeResult:
    """Structured result from one OpenCode CLI invocation."""

    exit_code: int
    events: list[OpenCodeEvent]
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def text(self) -> str:
        parts: list[str] = []

        for event in self.events:
            if event.type != "text":
                continue

            text = event.raw.get("part", {}).get("text")

            if isinstance(text, str):
                parts.append(text)

        return "".join(parts)


class OpenCodeClient:
    """Controlled adapter around the OpenCode CLI.

    :meth:`run` is the provider execution boundary: it receives the
    governing execution policy, enforces the read-only contract, and
    only then spawns the OpenCode process.
    """

    DEFAULT_EXECUTABLE = str(Path.home() / ".opencode" / "bin" / "opencode")
    DEFAULT_MODEL = "opencode/mimo-v2.5-free"

    #: Model-ID prefixes this backend can route.  OpenRouter IDs
    #: (``org/model:variant``) are a different namespace and must not
    #: be forwarded unchanged.  Subclasses or callers may extend this
    #: tuple to declare additional routable prefixes.
    ROUTABLE_ID_PREFIXES: tuple[str, ...] = ("opencode/",)

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: float = 120.0,
        routable_id_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        self.executable = executable or self.DEFAULT_EXECUTABLE
        self.timeout = timeout
        self._routable_id_prefixes = (
            routable_id_prefixes
            if routable_id_prefixes is not None
            else self.ROUTABLE_ID_PREFIXES
        )

    # ------------------------------------------------------------------
    # Capability boundary
    # ------------------------------------------------------------------

    def can_route(self, model_id: str) -> bool:
        """Return *True* if this backend can route *model_id*.

        The default implementation checks whether *model_id* starts with
        one of :pyattr:`ROUTABLE_ID_PREFIXES`.  Override or inject a
        custom prefix tuple to widen or narrow the boundary.
        """

        return any(model_id.startswith(p) for p in self._routable_id_prefixes)

    @staticmethod
    def _enforce_read_only(policy: ExecutionPolicy) -> None:
        """Refuse to start an execution the policy does not permit.

        This is the TASK-004 read-only boundary check.  It runs inside
        the provider, independently of any upstream validation, before
        the command is built or the OpenCode process is spawned:

        - an unknown/non-policy object is refused (fail closed);
        - any enabled write-capable capability (edit, shell, network,
          git, commit) is refused.
        """

        if not isinstance(policy, ExecutionPolicy):
            raise OpenCodePolicyError(
                "read-only execution boundary requires an "
                f"ExecutionPolicy; got {type(policy).__name__!r}"
            )

        enabled = write_capabilities(policy)

        if enabled:
            raise OpenCodePolicyError(
                "read-only execution boundary rejects write-capable "
                f"capabilities: {', '.join(enabled)}"
            )

    def run(
        self,
        prompt: str,
        workdir: str | Path,
        *,
        model: str | None = None,
        policy: ExecutionPolicy | None = None,
    ) -> OpenCodeResult:
        """Execute one OpenCode session under the governing *policy*.

        *policy* is the applicable :class:`router.policy.ExecutionPolicy`
        for this execution request.  ``policy=None`` is governed as
        :data:`router.policy.READ_ONLY` (fail-closed default): a
        write-capable policy is refused here, never silently downgraded
        or trusted to upstream validation.
        """

        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        self._enforce_read_only(READ_ONLY if policy is None else policy)

        command = [
            self.executable,
            "run",
            "--format",
            "json",
            "--model",
            model or self.DEFAULT_MODEL,
            prompt,
        ]

        try:
            completed = subprocess.run(
                command,
                cwd=Path(workdir),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenCodeTimeoutError(
                f"OpenCode exceeded {self.timeout} seconds."
            ) from exc
        except OSError as exc:
            raise OpenCodeError(
                f"Unable to execute OpenCode: {exc}"
            ) from exc

        events: list[OpenCodeEvent] = []

        for line in completed.stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(payload, dict):
                continue

            event_type = payload.get("type")

            if isinstance(event_type, str):
                events.append(
                    OpenCodeEvent(
                        type=event_type,
                        raw=payload,
                    )
                )

        return OpenCodeResult(
            exit_code=completed.returncode,
            events=events,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
