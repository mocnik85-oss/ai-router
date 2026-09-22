"""PM runtime — loading the relevant project context (MVP-001).

Before the PM runtime can turn a user request into a proposal it loads
the PM-owned project records that give the request its context:

    project/PROJECT_STATE.md   protocol + project state (PM-owned)
    project/TASKS.md           authoritative task register (PM-owned)
    MASTER_PLAN.md             human-readable plan (project version)

Authority rules this module keeps:

- **read-only**: the context loader never writes to those records, so
  PM state authority stays with the Project Manager — creating or
  approving a proposal does not edit the task register;
- **fail-closed**: a missing record, a missing required field, or a
  register entry without an exact V1.1 task state raises
  :class:`~pm.errors.PMContextError` instead of falling back to an
  assumed context;
- **backend-independent**: only the standard library plus
  ``protocol.artifacts`` is used — no ``router``/``providers`` module,
  no network, no credential access, no external action.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from pm.errors import PMContextError
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    TaskState,
    _require_identity,
)


#: ``## TASK-002 — Title`` headings in the authoritative register.
_TASK_HEADING_RE = re.compile(r"^##\s+(?P<task_id>TASK-\d+)\b")

#: ``- **Project version:** v1.1-migration`` bullets in MASTER_PLAN.md.
_PLAN_FIELD_RE = re.compile(r"^\s*[-*]\s+\*\*(?P<label>[^:*]+):\*\*\s*(?P<value>\S.*)$")


def _read_record(path: Path) -> str:
    """Read one PM-owned record, failing closed when it is unavailable."""

    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - message depends on OS
        raise PMContextError(f"cannot read project record {path.name}: {exc}") from exc


def _field(text: str, label: str, source: str) -> str:
    """Return the value of a ``Label: value`` line from *source*.

    The PM records use two equivalent shapes — the value inline
    (``Protocol Version: V1.1``) or on the following line
    (``Migration Branch:`` then ``protocol-v1.1-migration``) — both are
    accepted; anything else fails closed.
    """

    prefix = f"{label}:"
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        inline = stripped[len(prefix):].strip()
        if inline:
            return inline
        for follower in lines[index + 1:]:
            value = follower.strip()
            if value:
                return value
        break
    raise PMContextError(f"{source} does not record {label!r}")


def _plan_field(text: str, label: str, source: str) -> str:
    """Return the value of a ``- **Label:** value`` bullet from *source*."""

    for line in text.splitlines():
        match = _PLAN_FIELD_RE.match(line)
        if match and match.group("label").strip() == label:
            value = match.group("value").strip()
            if value:
                return value
    raise PMContextError(f"{source} does not record {label!r}")


@dataclass(frozen=True, slots=True)
class TaskRegisterEntry:
    """One task as recorded in the authoritative PM task register."""

    task_id: str
    title: str
    state: TaskState

    def __post_init__(self) -> None:
        _require_identity(self.task_id, "task_id")
        if not isinstance(self.title, str):
            raise ArtifactValidationError("title must be a string")
        if not isinstance(self.state, TaskState):
            raise ArtifactValidationError(
                "state must be an exact V1.1 task state"
            )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "state": str(self.state),
        }

    @classmethod
    def from_dict(cls, data: object) -> "TaskRegisterEntry":
        if not isinstance(data, dict):
            raise ArtifactValidationError(
                "task register entry must be an object"
            )
        try:
            task_id = data["task_id"]
            title = data["title"]
            state = data["state"]
        except KeyError as exc:
            raise ArtifactValidationError(
                f"task register entry is missing {exc.args[0]!r}"
            ) from exc
        if not isinstance(title, str):
            raise ArtifactValidationError("title must be a string")
        try:
            coerced_state = TaskState(state)
        except ValueError as exc:
            raise ArtifactValidationError(
                f"task register entry holds a non-V1.1 state: {state!r}"
            ) from exc
        return cls(task_id=task_id, title=title, state=coerced_state)


def _parse_register(text: str) -> tuple[TaskRegisterEntry, ...]:
    """Parse ``## TASK-00N`` / ``State:`` pairs out of the register."""

    headings: list[str] = []
    entries: list[TaskRegisterEntry] = []
    current_id: str | None = None
    current_title = ""

    for line in text.splitlines():
        heading = _TASK_HEADING_RE.match(line)
        if heading:
            task_id = heading.group("task_id")
            headings.append(task_id)
            current_id = task_id
            current_title = (
                line.split(task_id, 1)[1].strip().lstrip("—–-").strip()
            )
            continue

        if current_id is None:
            continue

        stripped = line.strip()
        if stripped.startswith("State:"):
            state_value = stripped.split(":", 1)[1].strip()
            try:
                state = TaskState(state_value)
            except ValueError as exc:
                raise PMContextError(
                    f"task register records a non-V1.1 state for "
                    f"{current_id}: {state_value!r}"
                ) from exc
            entries.append(
                TaskRegisterEntry(
                    task_id=current_id, title=current_title, state=state
                )
            )
            current_id = None

    if len(entries) != len(headings):
        missing = sorted(set(headings) - {e.task_id for e in entries})
        raise PMContextError(
            "task register contains entries without an exact V1.1 "
            f"State: {', '.join(missing)}"
        )
    if not entries:
        raise PMContextError("task register contains no tasks")
    return tuple(entries)


@dataclass(frozen=True, slots=True)
class ProjectContext:
    """The PM-owned project context a proposal must be grounded in.

    ``root`` is the project directory the records were loaded from. It
    is deliberately excluded from serialization and from equality: a
    proposal stores a context *snapshot*, not a machine-specific path.
    """

    project: ProjectArtifact
    protocol_version: str
    protocol_status: str
    current_project_state: str
    migration_branch: str
    tasks: tuple[TaskRegisterEntry, ...]
    root: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.project, ProjectArtifact):
            raise ArtifactValidationError(
                "project must be a ProjectArtifact"
            )
        for name in (
            "protocol_version",
            "protocol_status",
            "current_project_state",
            "migration_branch",
        ):
            _require_identity(getattr(self, name), name)
        if isinstance(self.tasks, (str, bytes)) or not isinstance(
            self.tasks, (tuple, list)
        ):
            raise ArtifactValidationError("tasks must be a sequence")
        for entry in self.tasks:
            if not isinstance(entry, TaskRegisterEntry):
                raise ArtifactValidationError(
                    "tasks must contain TaskRegisterEntry records"
                )
        object.__setattr__(self, "tasks", tuple(self.tasks))

    @classmethod
    def load(cls, root: Path | str) -> "ProjectContext":
        """Load the relevant PM-owned records under *root* (read-only)."""

        root = Path(root)
        state_text = _read_record(root / "project" / "PROJECT_STATE.md")
        register_text = _read_record(root / "project" / "TASKS.md")
        plan_text = _read_record(root / "MASTER_PLAN.md")

        project = ProjectArtifact(
            project_id=_field(state_text, "Project", "project/PROJECT_STATE.md"),
            project_version=_plan_field(
                plan_text, "Project version", "MASTER_PLAN.md"
            ),
        )
        context = cls(
            project=project,
            protocol_version=_field(
                state_text, "Protocol Version", "project/PROJECT_STATE.md"
            ),
            protocol_status=_field(
                state_text, "Protocol Status", "project/PROJECT_STATE.md"
            ),
            current_project_state=_field(
                state_text, "Current Project State", "project/PROJECT_STATE.md"
            ),
            migration_branch=_field(
                state_text, "Migration Branch", "project/PROJECT_STATE.md"
            ),
            tasks=_parse_register(register_text),
            root=root,
        )
        return context

    @property
    def requirement_version(self) -> str:
        """The requirement version a proposal is validated against.

        The governing V1.1 protocol version recorded by the Project
        Manager in ``project/PROJECT_STATE.md`` is the requirement
        version for this MVP.
        """

        return self.protocol_version

    @property
    def task_ids(self) -> tuple[str, ...]:
        """Task identities already present in the authoritative register."""

        return tuple(entry.task_id for entry in self.tasks)

    def to_dict(self) -> dict:
        """Machine-readable context snapshot (no filesystem paths)."""

        return {
            "project": {
                "project_id": self.project.project_id,
                "project_version": self.project.project_version,
            },
            "protocol_version": self.protocol_version,
            "protocol_status": self.protocol_status,
            "current_project_state": self.current_project_state,
            "migration_branch": self.migration_branch,
            "tasks": [entry.to_dict() for entry in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: object) -> "ProjectContext":
        if not isinstance(data, dict):
            raise ArtifactValidationError("context must be an object")
        try:
            project_data = data["project"]
            tasks_data = data["tasks"]
        except KeyError as exc:
            raise ArtifactValidationError(
                f"context is missing {exc.args[0]!r}"
            ) from exc
        if not isinstance(project_data, dict):
            raise ArtifactValidationError("context project must be an object")
        if isinstance(tasks_data, (str, bytes)) or not isinstance(
            tasks_data, (list, tuple)
        ):
            raise ArtifactValidationError("context tasks must be a sequence")
        try:
            return cls(
                project=ProjectArtifact(
                    project_id=project_data["project_id"],
                    project_version=project_data["project_version"],
                ),
                protocol_version=data["protocol_version"],
                protocol_status=data["protocol_status"],
                current_project_state=data["current_project_state"],
                migration_branch=data["migration_branch"],
                tasks=tuple(
                    TaskRegisterEntry.from_dict(entry) for entry in tasks_data
                ),
            )
        except KeyError as exc:
            raise ArtifactValidationError(
                f"context is missing {exc.args[0]!r}"
            ) from exc
