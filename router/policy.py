"""JEV v0.1 — Execution policy controls what an agent session may do."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """Declarative permissions for a single agent execution session."""

    read: bool = True
    edit: bool = False
    shell: bool = False
    network: bool = False
    git: bool = False
    commit: bool = False
    workspace: str | None = None
    timeout: float = 120.0
    max_attempts: int = 1


READ_ONLY = ExecutionPolicy()

# This is a harmless comment added to verify edit capability.
