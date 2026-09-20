from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
from typing import Any


class OpenCodeError(RuntimeError):
    """Base exception for OpenCode execution errors."""


class OpenCodeTimeoutError(OpenCodeError):
    """Raised when OpenCode exceeds its execution timeout."""


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
    """Controlled adapter around the OpenCode CLI."""

    DEFAULT_EXECUTABLE = str(Path.home() / ".opencode" / "bin" / "opencode")
    DEFAULT_MODEL = "opencode/mimo-v2.5-free"

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.executable = executable or self.DEFAULT_EXECUTABLE
        self.timeout = timeout

    def run(
        self,
        prompt: str,
        workdir: str | Path,
        *,
        model: str | None = None,
    ) -> OpenCodeResult:
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

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
