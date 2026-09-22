"""PM runtime — the machine-readable batch of proposals (MVP-002).

A :class:`PMBatch` is the PM-side representation of a *group* of
proposals the Project Manager reviews and approves as one unit,
before any execution or implementation handoff:

- ``proposals`` — the member :class:`pm.proposal.PMProposal` records the
  batch authorizes: a batch contains exactly the proposals (and
  therefore the proposed ``PENDING`` tasks) it speaks for, and nothing
  outside that membership is authorized by it;
- ``project`` / ``requirement_version`` — the traceability chain back
  to the project identity, the project version, and the requirement
  version every member is validated against: a member from another
  project, another project version, or another requirement version is
  refused instead of being silently mixed in;
- a deterministic, content-derived ``batch_id`` — the V1.1 idempotency
  pattern already used by MVP-001: project + requirement version +
  member set always derive the same identity, independent of member
  order, so re-proposing a batch is idempotent and never forks a second
  record or drops an approval;
- ``approval`` — the same explicit, immutable PM-side
  :class:`pm.proposal.ApprovalRecord` MVP-001 uses for a single
  proposal, reused here instead of a parallel approval model.  The
  absence of that record *is* the unapproved state the batch
  implementation gate refuses.

Approval is evidence of one Project-Manager decision over the whole
batch; it grants nothing.  A batch record carries no policy, no
permission, no credential, no workspace, and no backend field, and no
implementation backend is named, selected, imported, or reachable from
this module.  Batch approval is an *additional* PM act: it never
creates, replaces, or waives a member proposal's own approval, and it
never transitions a lifecycle state.

Member approval is deliberately **not** part of the embedded member
snapshot: approval stays authoritative in the proposal store and is
re-read by the runtime at every gate, so approving a proposal after its
batch was proposed never makes the batch record inconsistent.

Serialization is plain JSON (``to_dict`` / ``to_json`` / ``from_dict`` /
``from_json``) with a schema marker, so a batch is machine-readable and
self-describing on disk.  Members are serialized through the existing
MVP-001 proposal model — no parallel or duplicate proposal shape.

This module depends only on the standard library plus ``pm.proposal``
and ``protocol.artifacts``: no execution layer, no backend, no network,
no credential access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, NamedTuple

from pm.proposal import (
    PROPOSAL_ID_RE,
    ApprovalRecord,
    PMProposal,
)
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    _require_identity,
)

#: Schema marker stored with every machine-readable batch record.
BATCH_SCHEMA = "ai-router.pm.batch"

#: Schema version of the record written by this MVP.
BATCH_SCHEMA_VERSION = "1"

#: Exact shape of a stored batch identity (fail-closed for anything
#: else, so a caller-supplied identifier can never address a path
#: outside the batch store).
BATCH_ID_RE = re.compile(r"^BATCH-[0-9A-F]{16}$")


class BatchIdentity(NamedTuple):
    """Deterministic identity derived from project + requirement + members."""

    digest: str
    batch_id: str
    correlation_id: str
    idempotency_key: str


def _text(data: Mapping[str, Any], key: str) -> str:
    """Read one required string field of a batch record."""

    if key not in data:
        raise ArtifactValidationError(f"batch record is missing {key!r}")
    value = data[key]
    if not isinstance(value, str):
        raise ArtifactValidationError(
            f"batch record field {key!r} must be a string"
        )
    return value


def normalize_member_ids(proposal_ids: object) -> tuple[str, ...]:
    """Validate raw batch membership and return it in canonical order.

    Fail-closed: the membership must be a non-empty sequence of
    well-formed proposal identities with no repeat, so an empty batch, a
    duplicated member, a bare string, or a foreign identifier is
    refused before any identity is derived or any record is written.
    """

    if isinstance(proposal_ids, (str, bytes)) or not isinstance(
        proposal_ids, (tuple, list)
    ):
        raise ArtifactValidationError(
            "a batch membership must be a sequence of proposal identities"
        )
    members: list[str] = []
    for proposal_id in proposal_ids:
        if not isinstance(proposal_id, str) or not PROPOSAL_ID_RE.match(
            proposal_id
        ):
            raise ArtifactValidationError(
                f"batch member {proposal_id!r} is not a proposal identity"
            )
        members.append(proposal_id)
    if not members:
        raise ArtifactValidationError(
            "a batch must contain at least one proposal"
        )
    if len(set(members)) != len(members):
        raise ArtifactValidationError(
            "a batch must not repeat a proposal"
        )
    return tuple(sorted(members))


def derive_batch_identity(
    project: ProjectArtifact,
    requirement_version: str,
    proposal_ids: tuple[str, ...] | list[str],
) -> BatchIdentity:
    """Derive the stable identity of a batch (V1.1 idempotency).

    The digest covers project identity, project version, requirement
    version, and the canonical (sorted, de-duplicated only by the
    no-repeat rule above) member set, so:

    - the same member set in the same context maps to the same batch,
      whatever order the members are named in (re-proposing a batch
      never forks a second record or drops an approval);
    - a different member set, a different context, a different project
      version, or a different requirement version maps to a different
      batch.
    """

    if not isinstance(project, ProjectArtifact):
        raise ArtifactValidationError("project must be a ProjectArtifact")
    _require_identity(requirement_version, "requirement_version")
    members = normalize_member_ids(proposal_ids)

    payload = "\x1f".join(
        (
            project.project_id,
            project.project_version,
            requirement_version,
            "\x1e".join(members),
        )
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16].upper()
    return BatchIdentity(
        digest=digest,
        batch_id=f"BATCH-{digest}",
        correlation_id=f"CORR-BATCH-{digest}",
        idempotency_key=(
            f"batch:{project.project_id}:{project.project_version}:"
            f"{requirement_version}:{digest}"
        ),
    )


def proposal_content(proposal: PMProposal) -> dict:
    """The identity-relevant content of one member proposal.

    Member approval is deliberately excluded: approval stays
    authoritative in the proposal store, so a member approved after its
    batch was proposed still matches the batch record.
    """

    data = proposal.to_dict()
    data.pop("approved", None)
    data.pop("approval", None)
    return data


@dataclass(frozen=True, slots=True)
class PMBatch:
    """An explicit, approvable batch of PM proposals.

    Invariants enforced at construction (fail-closed):

    - required identities are non-empty (V1.1 ``_require_identity``);
    - ``proposals`` is a non-empty sequence of real
      :class:`~pm.proposal.PMProposal` records, normalized to canonical
      member order, with no repeated member and with well-formed
      proposal identities;
    - every member belongs to this batch's project identity *and*
      project version, and to this batch's requirement version, so a
      batch cannot mix contexts, cannot hold a foreign member, and
      cannot be relabelled after the fact;
    - ``batch_id``, ``correlation_id``, and ``idempotency_key`` equal
      exactly the values :func:`derive_batch_identity` derives from
      project + requirement version + member set: batch identity is
      deterministic, order-independent, and cannot be forged or
      mismatched;
    - ``approval`` is an :class:`~pm.proposal.ApprovalRecord` or
      ``None``; ``None`` means *unapproved*, which the batch
      implementation gate refuses.
    """

    batch_id: str
    correlation_id: str
    idempotency_key: str
    created_at: str
    project: ProjectArtifact
    requirement_version: str
    proposals: tuple[PMProposal, ...]
    approval: ApprovalRecord | None = None

    def __post_init__(self) -> None:
        for name in (
            "batch_id",
            "correlation_id",
            "idempotency_key",
            "created_at",
            "requirement_version",
        ):
            _require_identity(getattr(self, name), name)

        if not isinstance(self.project, ProjectArtifact):
            raise ArtifactValidationError("project must be a ProjectArtifact")
        if isinstance(self.proposals, (str, bytes)) or not isinstance(
            self.proposals, (tuple, list)
        ):
            raise ArtifactValidationError(
                "proposals must be a sequence of PM proposals"
            )
        for member in self.proposals:
            if not isinstance(member, PMProposal):
                raise ArtifactValidationError(
                    "proposals must contain PM proposal records"
                )
        members = tuple(sorted(self.proposals, key=lambda m: m.proposal_id))
        if not members:
            raise ArtifactValidationError(
                "a batch must contain at least one proposal"
            )

        member_ids = tuple(member.proposal_id for member in members)
        if len(set(member_ids)) != len(member_ids):
            raise ArtifactValidationError(
                "a batch must not repeat a proposal"
            )
        for proposal_id in member_ids:
            if not PROPOSAL_ID_RE.match(proposal_id):
                raise ArtifactValidationError(
                    f"batch member {proposal_id!r} is not a proposal identity"
                )

        for member in members:
            if member.project != self.project:
                raise ArtifactValidationError(
                    f"batch member {member.proposal_id} belongs to another "
                    "project or project version"
                )
            if member.requirement_version != self.requirement_version:
                raise ArtifactValidationError(
                    f"batch member {member.proposal_id} belongs to another "
                    "requirement version"
                )

        identity = derive_batch_identity(
            self.project, self.requirement_version, member_ids
        )
        if (
            identity.batch_id != self.batch_id
            or identity.correlation_id != self.correlation_id
            or identity.idempotency_key != self.idempotency_key
        ):
            raise ArtifactValidationError(
                f"batch identity {self.batch_id!r} does not match the "
                "identity derived from its members"
            )

        if self.approval is not None and not isinstance(
            self.approval, ApprovalRecord
        ):
            raise ArtifactValidationError(
                "approval must be an ApprovalRecord or None"
            )
        object.__setattr__(self, "proposals", members)

    @property
    def is_approved(self) -> bool:
        """Whether the Project Manager has explicitly approved this batch."""

        return self.approval is not None

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        """The identities of the proposals this batch authorizes."""

        return tuple(member.proposal_id for member in self.proposals)

    @property
    def store_filename(self) -> str:
        """File name this batch is persisted under."""

        return f"{self.batch_id}.json"

    def to_dict(self) -> dict:
        """Machine-readable representation of the whole batch."""

        return {
            "schema": BATCH_SCHEMA,
            "schema_version": BATCH_SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "requirement_version": self.requirement_version,
            "project": {
                "project_id": self.project.project_id,
                "project_version": self.project.project_version,
            },
            "proposals": [member.to_dict() for member in self.proposals],
            "approved": self.is_approved,
            "approval": (
                None if self.approval is None else self.approval.to_dict()
            ),
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the batch as JSON text."""

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: object) -> "PMBatch":
        """Rebuild a batch from its machine-readable record.

        Unknown schemas, unknown schema versions, missing fields,
        malformed membership, and inconsistent approval flags are all
        rejected: a tampered or foreign record never becomes a
        loadable batch.
        """

        if not isinstance(data, Mapping):
            raise ArtifactValidationError("batch record must be an object")

        schema = data.get("schema")
        if schema != BATCH_SCHEMA:
            raise ArtifactValidationError(
                f"unsupported batch schema: {schema!r}"
            )
        version = str(data.get("schema_version", ""))
        if version != BATCH_SCHEMA_VERSION:
            raise ArtifactValidationError(
                f"unsupported batch schema version: {version!r}"
            )

        project_data = data.get("project")
        if not isinstance(project_data, Mapping):
            raise ArtifactValidationError(
                "batch record field 'project' must be an object"
            )
        project = ProjectArtifact(
            project_id=project_data.get("project_id", ""),
            project_version=project_data.get("project_version", ""),
        )
        proposals_data = data.get("proposals")
        if isinstance(proposals_data, (str, bytes)) or not isinstance(
            proposals_data, (tuple, list)
        ):
            raise ArtifactValidationError(
                "batch record field 'proposals' must be a list of "
                "proposal records"
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
                "batch approval flag and approval record disagree"
            )

        return cls(
            batch_id=_text(data, "batch_id"),
            correlation_id=_text(data, "correlation_id"),
            idempotency_key=_text(data, "idempotency_key"),
            created_at=_text(data, "created_at"),
            project=project,
            requirement_version=_text(data, "requirement_version"),
            proposals=tuple(
                PMProposal.from_dict(member) for member in proposals_data
            ),
            approval=approval,
        )

    @classmethod
    def from_json(cls, text: str) -> "PMBatch":
        """Rebuild a batch from JSON text."""

        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                f"batch record is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)
