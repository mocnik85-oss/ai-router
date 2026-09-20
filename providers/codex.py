from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess


class CodexError(RuntimeError):
    """Base exception for Codex execution errors."""


class CodexTimeoutError(CodexError):
    """Raised when Codex exceeds its execution timeout."""


@dataclass(slots=True)
class CodexResult:
    """Structured result from one Codex CLI invocation."""

    exit_code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class CodexClient:
    """Small, controlled adapter around the Codex CLI."""

    DEFAULT_EXECUTABLE = str(Path.home() / ".local" / "bin" / "codex")
    DEFAULT_TIMEOUT = 300

    def __init__(
        self,
        executable: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        self.executable = executable or self.DEFAULT_EXECUTABLE
        self.timeout = timeout

    def run(
        self,
        prompt: str,
        working_directory: str | Path | None = None,
    ) -> CodexResult:
        if not prompt.strip():
            raise ValueError("prompt must not be empty")

        cwd = Path(working_directory or Path.cwd()).expanduser().resolve()

        if not cwd.is_dir():
            raise ValueError(f"working directory does not exist: {cwd}")

        command = [
            self.executable,
            "exec",
            prompt,
        ]

        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexTimeoutError(
                f"Codex timed out after {self.timeout} seconds"
            ) from exc
        except OSError as exc:
            raise CodexError(f"failed to start Codex: {exc}") from exc

        return CodexResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
