"""PM runtime — the authoritative PM-side execution entry point (MVP-001).

``PMRuntime`` is where a user text request becomes a proposal, where
the Project Manager records an approval, and where — and *only* where —
implementation may be handed to a backend:

    user text request
        -> ProjectContext.load          (PM-owned records, read-only)
        -> PMRuntime.propose            (structured PMProposal, persisted
                                          under project/proposals/)
        -> PMRuntime.approve            (explicit PM approval record)
        -> PMRuntime.handoff            (the single implementation gate)

The gate is fail-closed and does exactly three things, in order:

1. refuse a request that names no stored proposal
   (:class:`~pm.errors.ProposalNotFoundError`) — implementation may not
   be invoked before a proposal exists;
2. refuse a proposal with no approval record
   (:class:`~pm.errors.ProposalNotApprovedError`) — an unapproved
   proposal never reaches an implementation backend;
3. only then call the caller-supplied backend with the proposal.

Authority boundaries kept by this module:

- **PM state authority stays on this side.** Proposals are persisted as
  PM-owned records; ``project/TASKS.md`` and
  ``project/PROJECT_STATE.md`` are read-only inputs that are never
  rewritten by proposing, approving, or handing off.  Nothing here
  interprets results or applies lifecycle transitions — that remains
  ``protocol.interpretation`` + the Project Manager.
- **Backend mechanics stay out.** The backend is an opaque callable
  supplied by the caller: no ``router``/``providers`` import, no
  hard-coded execution backend, no backend-specific interface (the
  implementer contract is later MVP work).
- **No authority is granted.** Approval authorizes the handoff of one
  proposal, nothing else: this module holds no execution policy, no
  workspace, no credential, no network access, and no external action.
  It passes exactly one argument — the proposal — to the backend, and
  returns the backend's raw return value untouched.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from pm.context import ProjectContext
from pm.errors import (
    PMRuntimeError,
    ProposalAlreadyApprovedError,
    ProposalNotFoundError,
    ProposalNotApprovedError,
)
from pm.proposal import (
    PROPOSAL_ID_RE,
    ApprovalRecord,
    PMProposal,
    default_acceptance_criteria,
    default_verification,
    derive_identity,
    _summarize,
)
from protocol.artifacts import (
    ArtifactValidationError,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
    _require_identity,
)


def default_project_root() -> Path:
    """The project directory this repository is checked out in."""

    return Path(__file__).resolve().parent.parent


def _utc_now() -> str:
    """Current UTC timestamp in a stable, machine-readable form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProposalStore:
    """Durable, machine-readable proposal records (JSON under the project).

    One proposal is one file: ``project/proposals/<PROPOSAL_ID>.json``.
    Identities are validated against the exact ``PROP-<16 hex>`` shape
    before any path is built, so a caller-supplied identifier can never
    address a file outside the store.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else default_project_root()

    @property
    def root(self) -> Path:
        """The project root the store lives under."""

        return self._root

    @property
    def directory(self) -> Path:
        """The PM-owned proposal directory."""

        return self._root / "project" / "proposals"

    @staticmethod
    def validate_id(proposal_id: object) -> str:
        """Return *proposal_id* when it is a well-formed proposal identity."""

        if not isinstance(proposal_id, str) or not PROPOSAL_ID_RE.match(
            proposal_id
        ):
            raise ProposalNotFoundError(
                f"no proposal exists for identity {proposal_id!r}"
            )
        return proposal_id

    def path_for(self, proposal_id: str) -> Path:
        """Absolute path of one proposal record (fail-closed on identity)."""

        return self.directory / f"{self.validate_id(proposal_id)}.json"

    def exists(self, proposal_id: str) -> bool:
        """Whether a stored proposal record exists for *proposal_id*."""

        return self.path_for(proposal_id).is_file()

    def load(self, proposal_id: str) -> PMProposal:
        """Load one stored proposal, failing closed when it is unreadable."""

        path = self.path_for(proposal_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ProposalNotFoundError(
                f"no proposal exists for identity {proposal_id!r}"
            ) from exc
        except OSError as exc:  # pragma: no cover - message depends on OS
            raise PMRuntimeError(
                f"proposal record {path.name} is unreadable: {exc}"
            ) from exc
        try:
            proposal = PMProposal.from_json(text)
        except ArtifactValidationError as exc:
            raise PMRuntimeError(
                f"proposal record {path.name} is invalid: {exc}"
            ) from exc
        if proposal.proposal_id != proposal_id:
            raise PMRuntimeError(
                f"proposal record {path.name} holds a different identity "
                f"({proposal.proposal_id!r})"
            )
        return proposal

    def save(self, proposal: PMProposal) -> Path:
        """Persist *proposal* as machine-readable JSON; return its path."""

        if not isinstance(proposal, PMProposal):
            raise ArtifactValidationError(
                "only a PMProposal can be persisted by the proposal store"
            )
        path = self.path_for(proposal.proposal_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(proposal.to_json() + "\n", encoding="utf-8")
        return path


class PMRuntime:
    """Authoritative PM-side runtime: context -> proposal -> gate.

    One instance owns one project root.  It is the only place in this
    MVP where a request becomes a proposal and where an implementation
    handoff is authorized; everything else (execution, orchestration,
    interpretation) stays outside this package.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        store: ProposalStore | None = None,
        context: ProjectContext | None = None,
    ) -> None:
        self._root = Path(root) if root is not None else default_project_root()
        self._store = store if store is not None else ProposalStore(self._root)
        self._context = context

    @property
    def root(self) -> Path:
        """The project root this runtime governs."""

        return self._root

    @property
    def store(self) -> ProposalStore:
        """The durable proposal store this runtime writes to."""

        return self._store

    @property
    def context(self) -> ProjectContext:
        """The loaded PM-owned project context (cached after first load)."""

        if self._context is None:
            self._context = ProjectContext.load(self._root)
        return self._context

    def propose(
        self,
        request: str,
        *,
        acceptance_criteria: Sequence[str] | None = None,
        verification: VerificationDefinition | None = None,
    ) -> PMProposal:
        """Turn a user text request into a persisted PM proposal.

        Steps:

        1. validate the request;
        2. load the relevant PM-owned project context (fail-closed);
        3. derive the deterministic proposal identity;
        4. return the existing record unchanged when the same request in
           the same context was proposed before — an approval is never
           dropped by re-proposing;
        5. otherwise build the structured proposal around a ``PENDING``
           :class:`~protocol.artifacts.TaskArtifact` and persist it.

        The proposal is created *before* any implementation handoff can
        be attempted, and creating it changes no register file.
        """

        if not isinstance(request, str) or not request.strip():
            raise ArtifactValidationError(
                "request must be a non-empty text request"
            )
        normalized = request.strip()

        context = self.context
        identity = derive_identity(
            context.project, context.requirement_version, normalized
        )
        if self._store.exists(identity.proposal_id):
            return self._store.load(identity.proposal_id)

        criteria = (
            default_acceptance_criteria(normalized)
            if acceptance_criteria is None
            else tuple(acceptance_criteria)
        )
        verification_definition = (
            default_verification() if verification is None else verification
        )

        task = TaskArtifact(
            task_id=identity.task_id,
            task_version="v1",
            requirement_version=context.requirement_version,
            state=TaskState.PENDING,
            acceptance_criteria=criteria,
            dependencies=(),
            verification=verification_definition,
        )

        proposal = PMProposal(
            proposal_id=identity.proposal_id,
            correlation_id=identity.correlation_id,
            idempotency_key=identity.idempotency_key,
            created_at=_utc_now(),
            project=context.project,
            requirement_version=context.requirement_version,
            request=normalized,
            summary=_summarize(normalized),
            task=task,
            context=context,
            approval=None,
        )
        self._store.save(proposal)
        return proposal

    def get(self, proposal_id: str) -> PMProposal:
        """Load a stored proposal; unknown identities fail closed."""

        return self._store.load(proposal_id)

    def approve(
        self, proposal_id: str, *, approver: str, note: str = ""
    ) -> PMProposal:
        """Record the explicit PM approval for one stored proposal.

        This is a Project-Manager act persisted on the proposal record.
        It creates no lifecycle state, changes no task state, grants no
        permission, and cannot be overwritten once recorded.
        """

        _require_identity(approver, "approver")
        if not isinstance(note, str):
            raise ArtifactValidationError("note must be a string")

        proposal = self.get(proposal_id)
        if proposal.is_approved:
            raise ProposalAlreadyApprovedError(
                f"proposal {proposal.proposal_id} is already approved by "
                f"{proposal.approval.approver!r}"
            )
        approved = replace(
            proposal,
            approval=ApprovalRecord(
                approver=approver, approved_at=_utc_now(), note=note
            ),
        )
        self._store.save(approved)
        return approved

    def handoff(
        self,
        proposal_id: str,
        backend: Callable[[PMProposal], Any],
    ) -> Any:
        """The single, gated implementation handoff (fail-closed).

        ``backend`` is an opaque callable supplied by the caller — this
        runtime neither selects nor imports an execution backend.

        Raises before any backend call:

        - :class:`TypeError`-equivalent failure when ``backend`` is not
          callable (:class:`~pm.errors.PMRuntimeError`);
        - :class:`~pm.errors.ProposalNotFoundError` when no proposal is
          stored for ``proposal_id`` — implementation cannot be invoked
          before a proposal exists;
        - :class:`~pm.errors.ProposalNotApprovedError` when the proposal
          carries no approval record — an unapproved proposal never
          reaches an implementation backend.

        On success the backend receives exactly one argument (the
        proposal) and its raw return value is returned untouched: it is
        not interpreted, not recorded as evidence, and never becomes
        authoritative task/project state on its own.
        """

        if not callable(backend):
            raise PMRuntimeError(
                "backend must be a callable accepting one PMProposal"
            )
        proposal = self.get(proposal_id)
        if not proposal.is_approved:
            raise ProposalNotApprovedError(
                f"proposal {proposal.proposal_id} is not approved; "
                "implementation handoff refused"
            )
        return backend(proposal)
