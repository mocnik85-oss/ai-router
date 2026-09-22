"""Interrogator AI-Assisted Project Lifecycle Protocol V1.1 — artifact model.

The minimal authoritative artifacts required by Protocol V1.1:

- project identity and version
- task identity, version, requirement version, lifecycle state,
  acceptance criteria, dependencies, and verification definition
- handoff identity and lifecycle state
- execution identity, correlation ID, and idempotency key

Lifecycle states are represented exactly as defined by V1.1; no
additional states may be invented.  Required identity fields are
validated at construction time.

This module is deliberately independent of ``router``/JEV so that
authoritative governance state never depends on execution code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactValidationError(ValueError):
    """Raised when a required artifact identity or lifecycle state is invalid."""


class TaskState(StrEnum):
    """The exact V1.1 task lifecycle states. No other states are permitted."""

    PENDING = "PENDING"
    READY = "READY"
    IMPLEMENTING = "IMPLEMENTING"
    VERIFYING = "VERIFYING"
    AUDITING = "AUDITING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    NEEDS_USER = "NEEDS_USER"
    CANCELLED = "CANCELLED"
    OBSOLETE = "OBSOLETE"


class HandoffState(StrEnum):
    """The exact V1.1 handoff lifecycle states. No other states are permitted."""

    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    IN_PROGRESS = "IN_PROGRESS"
    RESULT_READY = "RESULT_READY"
    PROCESSED = "PROCESSED"
    SUPERSEDED = "SUPERSEDED"


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------

def _require_identity(value: object, field_name: str) -> None:
    """Reject missing, non-string, or blank required identity fields."""

    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(
            f"{field_name} must be a non-empty identity string"
        )


def _require_entries(value: object, field_name: str) -> tuple[str, ...]:
    """Validate a sequence of strings (criteria or referenced identities)."""

    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ArtifactValidationError(
            f"{field_name} must be a sequence of strings, not a single value"
        )

    entries = tuple(value)
    for entry in entries:
        if not isinstance(entry, str) or not entry.strip():
            raise ArtifactValidationError(
                f"{field_name} contains an invalid entry: {entry!r}"
            )
    return entries


def _coerce_state(value: object, state_type: type, field_name: str) -> object:
    """Coerce *value* to a V1.1 lifecycle state, rejecting invented states."""

    if isinstance(value, state_type):
        return value

    if isinstance(value, str):
        try:
            return state_type(value)
        except ValueError:
            pass

    allowed = ", ".join(member.name for member in state_type)
    raise ArtifactValidationError(
        f"{field_name} must be one of [{allowed}]; got {value!r}"
    )


# ------------------------------------------------------------------
# Artifacts
# ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class VerificationDefinition:
    """How a task's acceptance criteria must be verified."""

    method: str
    description: str = ""

    def __post_init__(self) -> None:
        _require_identity(self.method, "method")
        if not isinstance(self.description, str):
            raise ArtifactValidationError("description must be a string")


@dataclass(frozen=True, slots=True)
class ProjectArtifact:
    """Authoritative project identity and version."""

    project_id: str
    project_version: str

    def __post_init__(self) -> None:
        _require_identity(self.project_id, "project_id")
        _require_identity(self.project_version, "project_version")


@dataclass(frozen=True, slots=True)
class TaskArtifact:
    """Authoritative task record.

    Carries task identity/version, the requirement version the task is
    validated against, the exact V1.1 lifecycle state, acceptance
    criteria, dependencies (referenced task identities), and the
    verification definition.
    """

    task_id: str
    task_version: str
    requirement_version: str
    state: TaskState = TaskState.PENDING
    acceptance_criteria: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    verification: VerificationDefinition | None = None

    def __post_init__(self) -> None:
        _require_identity(self.task_id, "task_id")
        _require_identity(self.task_version, "task_version")
        _require_identity(self.requirement_version, "requirement_version")
        object.__setattr__(
            self, "state", _coerce_state(self.state, TaskState, "state")
        )
        object.__setattr__(
            self,
            "acceptance_criteria",
            _require_entries(self.acceptance_criteria, "acceptance_criteria"),
        )
        object.__setattr__(
            self, "dependencies", _require_entries(self.dependencies, "dependencies")
        )
        if self.verification is not None and not isinstance(
            self.verification, VerificationDefinition
        ):
            raise ArtifactValidationError(
                "verification must be a VerificationDefinition or None"
            )


@dataclass(frozen=True, slots=True)
class HandoffArtifact:
    """Authoritative handoff: identity, correlation, and lifecycle state."""

    handoff_id: str
    correlation_id: str
    state: HandoffState = HandoffState.PENDING

    def __post_init__(self) -> None:
        _require_identity(self.handoff_id, "handoff_id")
        _require_identity(self.correlation_id, "correlation_id")
        object.__setattr__(
            self, "state", _coerce_state(self.state, HandoffState, "state")
        )


@dataclass(frozen=True, slots=True)
class ExecutionArtifact:
    """Authoritative execution identity, correlation ID, and idempotency key."""

    execution_id: str
    correlation_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_identity(self.execution_id, "execution_id")
        _require_identity(self.correlation_id, "correlation_id")
        _require_identity(self.idempotency_key, "idempotency_key")
