"""JEV v0.1 — Execution policy controls what an agent session may do.

This module is the single source of truth for the read-only execution
contract.  The same definition is used by the orchestrator's upstream
validation (``router.jev.JEV._validate_policy``) and by the provider
execution boundary (``providers.opencode.OpenCodeClient.run``), so the
two enforcement points can never drift apart.
"""

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

#: Capabilities that can modify state or act beyond a read-only session.
#: An execution governed as read-only must have none of these enabled.
WRITE_CAPABLE_CAPABILITIES: tuple[str, ...] = (
    "edit",
    "shell",
    "network",
    "git",
    "commit",
)


def write_capabilities(policy: ExecutionPolicy) -> tuple[str, ...]:
    """Return the write-capable capabilities enabled by *policy*.

    Preserves the order of :data:`WRITE_CAPABLE_CAPABILITIES` so
    upstream and boundary error messages stay identical.  A foreign
    object without the capability attributes still raises
    ``AttributeError``, as before.
    """

    return tuple(
        name for name in WRITE_CAPABLE_CAPABILITIES if getattr(policy, name)
    )


# This is a harmless comment added to verify edit capability.
