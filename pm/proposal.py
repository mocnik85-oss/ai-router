"""PM runtime — the structured, machine-readable PM proposal (MVP-001).

A :class:`PMProposal` is the PM-side representation of one user text
request turned into work the Project Manager can review.  It is built
from the existing Protocol V1.1 artifacts instead of a parallel model:

- :class:`protocol.artifacts.ProjectArtifact` — which project/version
  the request belongs to;
- :class:`protocol.artifacts.TaskArtifact` — the *proposed* task: its
  identity, requirement version, acceptance criteria, dependencies, and
  :class:`protocol.artifacts.VerificationDefinition`;
- :class:`protocol.artifacts.TaskState` — the exact V1.1 task states.
  A proposal is always ``PENDING``: it never claims implemented work
  and it never invents a lifecycle state;
- the V1.1 identity vocabulary — ``correlation_id`` and an
  ``idempotency_key`` — plus a deterministic, content-derived
  ``proposal_id`` so re-proposing the same request in the same context
  is idempotent.

Approval is represented as an explicit, immutable PM-side record
(``approval``), never as a new lifecycle state.  The absence of that
record *is* the unapproved state the implementation gate refuses.

Serialization is plain JSON (``to_dict`` / ``to_json`` /
``from_dict`` / ``from_json``) with a schema marker, so the proposal is
machine-readable and self-describing on disk.

This module depends only on the standard library plus
``protocol.artifacts``: no ``router``/``providers`` module, no backend,
no network, no credential access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple

from pm.context import ProjectContext
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
    _require_identity,
)

#: Schema marker stored with every machine-readable proposal record.
PROPOSAL_SCHEMA = "ai-router.pm.proposal"

#: Schema version of the record written by this MVP.
PROPOSAL_SCHEMA_VERSION = "1"

#: Exact shape of a stored proposal identity (fail-closed for anything
#: else, so a caller-supplied identifier can never address a path
#: outside the proposal store).
PROPOSAL_ID_RE = re.compile(r"^PROP-[0-9A-F]{16}$")

#: Maximum length of the human-readable summary line.
_SUMMARY_LIMIT = 120


class ProposalIdentity(NamedTuple):
    """Deterministic identity derived from project + requirement + request."""

    digest: str
    proposal_id: str
    correlation_id: str
    idempotency_key: str
    task_id: str


def _normalize_request(request: object) -> str:
    """Validate a user text request and return its normalized form."""

    if not isinstance(request, str) or not request.strip():
        raise ArtifactValidationError(
            "request must be a non-empty text request"
        )
    return request.strip()


def _summarize(request: str) -> str:
    """Collapse a request into one bounded summary line."""

    collapsed = " ".join(request.split())
    if len(collapsed) <= _SUMMARY_LIMIT:
        return collapsed
    return collapsed[: _SUMMARY_LIMIT - 3] + "..."


def derive_identity(
    project: ProjectArtifact, requirement_version: str, request: str
) -> ProposalIdentity:
    """Derive the stable identity of a proposal (V1.1 idempotency).

    The digest covers project identity, project version, requirement
    version, and the normalized request, so:

    - the same request in the same context maps to the same proposal
      (re-proposing never forks a second record or drops an approval);
    - a different context or a different request maps to a different
      proposal.
    """

    if not isinstance(project, ProjectArtifact):
        raise ArtifactValidationError("project must be a ProjectArtifact")
    _require_identity(requirement_version, "requirement_version")
    normalized = _normalize_request(request)

    payload = "\x1f".join(
        (
            project.project_id,
            project.project_version,
            requirement_version,
            normalized,
        )
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16].upper()
    return ProposalIdentity(
        digest=digest,
        proposal_id=f"PROP-{digest}",
        correlation_id=f"CORR-{digest}",
        idempotency_key=(
            f"proposal:{project.project_id}:{project.project_version}:"
            f"{requirement_version}:{digest}"
        ),
        task_id=f"TASK-PROP-{digest[:8]}",
    )


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """The explicit PM-side approval of exactly one proposal.

    Approval is evidence, not a lifecycle state: it records *who*
    approved and *when*.  It grants nothing by itself — in particular
    it carries no policy, permission, credential, or backend field.
    """

    approver: str
    approved_at: str
    note: str = ""

    def __post_init__(self) -> None:
        _require_identity(self.approver, "approver")
        _require_identity(self.approved_at, "approved_at")
        if not isinstance(self.note, str):
            raise ArtifactValidationError("note must be a string")

    def to_dict(self) -> dict:
        return {
            "approver": self.approver,
            "approved_at": self.approved_at,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ApprovalRecord":
        if not isinstance(data, dict):
            raise ArtifactValidationError("approval must be an object")
        try:
            return cls(
                approver=data["approver"],
                approved_at=data["approved_at"],
                note=data.get("note", ""),
            )
        except KeyError as exc:
            raise ArtifactValidationError(
                f"approval is missing {exc.args[0]!r}"
            ) from exc


def default_acceptance_criteria(request: str) -> tuple[str, ...]:
    """PM default acceptance criteria derived from the request text.

    The verbatim request stays traceable through the V1.1 chain
    (requirement -> acceptance criterion -> verification); the proposal
    stays unapproved until the Project Manager accepts or rewrites it.
    """

    return (
        f"The request is satisfied exactly as stated: {request}",
        "The focused tests and the existing repository test suite pass.",
    )


def default_verification() -> VerificationDefinition:
    """PM default verification definition for proposed coding work."""

    return VerificationDefinition(
        method="Automated tests",
        description=(
            "Focused tests, full regression suite, and Python compile "
            "verification"
        ),
    )


def _mapping(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in data:
        raise ArtifactValidationError(f"proposal record is missing {key!r}")
    value = data[key]
    if not isinstance(value, Mapping):
        raise ArtifactValidationError(f"proposal record field {key!r} must be an object")
    return value


def _text(data: Mapping[str, Any], key: str) -> str:
    if key not in data:
        raise ArtifactValidationError(f"proposal record is missing {key!r}")
    value = data[key]
    if not isinstance(value, str):
        raise ArtifactValidationError(f"proposal record field {key!r} must be a string")
    return value


def _task_from_dict(data: object) -> TaskArtifact:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("proposal task must be an object")
    verification_data = data.get("verification")
    if verification_data is None:
        verification = None
    elif isinstance(verification_data, Mapping):
        verification = VerificationDefinition(
            method=verification_data.get("method", ""),
            description=verification_data.get("description", ""),
        )
    else:
        raise ArtifactValidationError("verification must be an object or null")
    return TaskArtifact(
        task_id=data.get("task_id", ""),
        task_version=data.get("task_version", ""),
        requirement_version=data.get("requirement_version", ""),
        state=data.get("state", ""),
        acceptance_criteria=tuple(data.get("acceptance_criteria", ()) or ()),
        dependencies=tuple(data.get("dependencies", ()) or ()),
        verification=verification,
    )


@dataclass(frozen=True, slots=True)
class PMProposal:
    """One structured PM proposal for one user text request.

    Invariants enforced at construction (fail-closed):

    - required identities are non-empty (V1.1 ``_require_identity``);
    - ``task`` is a real :class:`TaskArtifact` whose requirement
      version matches the proposal and whose state is ``PENDING`` — a
      proposal never asserts completed work and never invents a state;
    - the proposed task carries at least one acceptance criterion;
    - ``project`` matches the project context the proposal was grounded
      in, so a record cannot be relabelled after the fact;
    - ``approval`` is an :class:`ApprovalRecord` or ``None``; ``None``
      means *unapproved*, which the implementation gate refuses.
    """

    proposal_id: str
    correlation_id: str
    idempotency_key: str
    created_at: str
    project: ProjectArtifact
    requirement_version: str
    request: str
    summary: str
    task: TaskArtifact
    context: ProjectContext
    approval: ApprovalRecord | None = None

    def __post_init__(self) -> None:
        for name in (
            "proposal_id",
            "correlation_id",
            "idempotency_key",
            "created_at",
            "requirement_version",
            "request",
            "summary",
        ):
            _require_identity(getattr(self, name), name)

        if not isinstance(self.project, ProjectArtifact):
            raise ArtifactValidationError("project must be a ProjectArtifact")
        if not isinstance(self.task, TaskArtifact):
            raise ArtifactValidationError("task must be a TaskArtifact")
        if self.task.requirement_version != self.requirement_version:
            raise ArtifactValidationError(
                "task requirement_version must match the proposal "
                "requirement_version"
            )
        if self.task.state is not TaskState.PENDING:
            raise ArtifactValidationError(
                "a proposal may only carry a PENDING task; it never "
                "asserts implemented work"
            )
        if not self.task.acceptance_criteria:
            raise ArtifactValidationError(
                "a proposal requires at least one acceptance criterion"
            )

        if not isinstance(self.context, ProjectContext):
            raise ArtifactValidationError("context must be a ProjectContext")
        if self.context.project != self.project:
            raise ArtifactValidationError(
                "proposal project identity must match its project context"
            )

        if self.approval is not None and not isinstance(
            self.approval, ApprovalRecord
        ):
            raise ArtifactValidationError(
                "approval must be an ApprovalRecord or None"
            )

    @property
    def is_approved(self) -> bool:
        """Whether the Project Manager has explicitly approved this proposal."""

        return self.approval is not None

    @property
    def store_filename(self) -> str:
        """File name this proposal is persisted under."""

        return f"{self.proposal_id}.json"

    def to_dict(self) -> dict:
        """Machine-readable representation of the whole proposal."""

        context = self.context
        return {
            "schema": PROPOSAL_SCHEMA,
            "schema_version": PROPOSAL_SCHEMA_VERSION,
            "proposal_id": self.proposal_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "requirement_version": self.requirement_version,
            "project": {
                "project_id": self.project.project_id,
                "project_version": self.project.project_version,
            },
            "request": self.request,
            "summary": self.summary,
            "task": {
                "task_id": self.task.task_id,
                "task_version": self.task.task_version,
                "requirement_version": self.task.requirement_version,
                "state": str(self.task.state),
                "acceptance_criteria": list(self.task.acceptance_criteria),
                "dependencies": list(self.task.dependencies),
                "verification": (
                    None
                    if self.task.verification is None
                    else {
                        "method": self.task.verification.method,
                        "description": self.task.verification.description,
                    }
                ),
            },
            "context": context.to_dict(),
            "approved": self.is_approved,
            "approval": (
                None if self.approval is None else self.approval.to_dict()
            ),
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the proposal as JSON text."""

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: object) -> "PMProposal":
        """Rebuild a proposal from its machine-readable record.

        Unknown schemas, unknown schema versions, missing fields, and
        inconsistent approval flags are all rejected: a tampered or
        foreign record never becomes a loadable proposal.
        """

        if not isinstance(data, Mapping):
            raise ArtifactValidationError("proposal record must be an object")

        schema = data.get("schema")
        if schema != PROPOSAL_SCHEMA:
            raise ArtifactValidationError(
                f"unsupported proposal schema: {schema!r}"
            )
        version = str(data.get("schema_version", ""))
        if version != PROPOSAL_SCHEMA_VERSION:
            raise ArtifactValidationError(
                f"unsupported proposal schema version: {version!r}"
            )

        project_data = _mapping(data, "project")
        project = ProjectArtifact(
            project_id=project_data.get("project_id", ""),
            project_version=project_data.get("project_version", ""),
        )
        approval_data = data.get("approval")
        approval = (
            None
            if approval_data is None
            else ApprovalRecord.from_dict(approval_data)
        )
        approved_flag = bool(data.get("approved", False))
        if approved_flag != (approval is not None):
            raise ArtifactValidationError(
                "proposal approval flag and approval record disagree"
            )

        return cls(
            proposal_id=_text(data, "proposal_id"),
            correlation_id=_text(data, "correlation_id"),
            idempotency_key=_text(data, "idempotency_key"),
            created_at=_text(data, "created_at"),
            project=project,
            requirement_version=_text(data, "requirement_version"),
            request=_text(data, "request"),
            summary=_text(data, "summary"),
            task=_task_from_dict(data.get("task")),
            context=ProjectContext.from_dict(data.get("context")),
            approval=approval,
        )

    @classmethod
    def from_json(cls, text: str) -> "PMProposal":
        """Rebuild a proposal from JSON text."""

        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                f"proposal record is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)
