"""PM runtime — the authoritative PM-side execution entry point
(MVP-001, MVP-002).

``PMRuntime`` is where a user text request becomes a proposal, where
the Project Manager records an approval, where a group of proposals
becomes an approvable batch, and where — and *only* where —
implementation may be handed to a backend:

    user text request
        -> ProjectContext.load          (PM-owned records, read-only)
        -> PMRuntime.propose            (structured PMProposal, persisted
                                          under project/proposals/)
        -> PMRuntime.approve            (explicit PM approval record)
        -> PMRuntime.handoff            (the single implementation gate)
        -> PMRuntime.propose_batch      (a deterministic batch of stored
                                          proposals, project/batches/)
        -> PMRuntime.approve_batch      (explicit PM batch approval)
        -> PMRuntime.handoff_batch      (the batch implementation gate)

The gate is fail-closed and does exactly four things, in order:

1. refuse a request that names no stored proposal
   (:class:`~pm.errors.ProposalNotFoundError`) — implementation may not
   be invoked before a proposal exists;
2. refuse a proposal with no approval record
   (:class:`~pm.errors.ProposalNotApprovedError`) — an unapproved
   proposal never reaches an implementation backend;
3. refuse a proposal that belongs to a stored batch with no batch
   approval record (:class:`~pm.errors.BatchNotApprovedError`) — no
   execution or handoff may occur before the batch that authorizes the
   proposal is approved (MVP-002);
4. only then call the caller-supplied backend with the proposal.

The batch gate (:meth:`PMRuntime.handoff_batch`) is the same shape: an
unknown batch, an unapproved batch, a batch whose stored members have
drifted or disappeared, and a batch containing any member proposal
without its own approval are all refused *before* the backend is
reached.  Batch approval never substitutes for a member approval, and
no approval is ever overwritten.

Authority boundaries kept by this module:

- **PM state authority stays on this side.** Proposals are persisted as
  PM-owned records; ``project/TASKS.md`` and
  ``project/PROJECT_STATE.md`` are read-only inputs that are never
  rewritten by proposing, approving, or handing off.  Nothing here
  interprets results or applies lifecycle transitions — that remains
  ``protocol.interpretation`` + the Project Manager.
- **Backend mechanics stay out.** The backend is an opaque callable
  supplied by the caller: no ``router``/``providers`` import, no
  hard-coded execution backend, no backend-specific interface.  This
  module also does not depend on ``protocol.implementer``: it hands
  off PM-side records only, never executing anything itself.
- **No authority is granted.** Approval — of one proposal or of a
  batch — authorizes handing off exactly that approved record and
  nothing else: this module holds no execution policy, no workspace,
  no credential, no network access, and no external action.  It passes
  exactly one argument (the proposal, or the batch) to the backend, and
  returns the backend's raw return value untouched.
- **PM authority is not delegated.** Nothing in the batch mechanism
  hands an authoritative state decision to a backend: a backend is
  never asked whether a batch is approved, never writes an approval,
  and never becomes the source of the batch identity or membership —
  those stay PM-side records derived from the project context.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from pm.batch import (
    BATCH_ID_RE,
    PMBatch,
    derive_batch_identity,
    normalize_member_ids,
    proposal_content,
)
from pm.context import ProjectContext
from pm.errors import (
    BatchAlreadyApprovedError,
    BatchNotFoundError,
    BatchNotApprovedError,
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


class BatchStore:
    """Durable, machine-readable batch records (JSON under the project).

    One batch is one file: ``project/batches/<BATCH_ID>.json``.
    Identities are validated against the exact ``BATCH-<16 hex>`` shape
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
        """The PM-owned batch directory."""

        return self._root / "project" / "batches"

    @staticmethod
    def validate_id(batch_id: object) -> str:
        """Return *batch_id* when it is a well-formed batch identity."""

        if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
            raise BatchNotFoundError(
                f"no batch exists for identity {batch_id!r}"
            )
        return batch_id

    def path_for(self, batch_id: str) -> Path:
        """Absolute path of one batch record (fail-closed on identity)."""

        return self.directory / f"{self.validate_id(batch_id)}.json"

    def exists(self, batch_id: str) -> bool:
        """Whether a stored batch record exists for *batch_id*."""

        return self.path_for(batch_id).is_file()

    def load(self, batch_id: str) -> PMBatch:
        """Load one stored batch, failing closed when it is unreadable."""

        path = self.path_for(batch_id)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise BatchNotFoundError(
                f"no batch exists for identity {batch_id!r}"
            ) from exc
        except OSError as exc:  # pragma: no cover - message depends on OS
            raise PMRuntimeError(
                f"batch record {path.name} is unreadable: {exc}"
            ) from exc
        try:
            batch = PMBatch.from_json(text)
        except ArtifactValidationError as exc:
            raise PMRuntimeError(
                f"batch record {path.name} is invalid: {exc}"
            ) from exc
        if batch.batch_id != batch_id:
            raise PMRuntimeError(
                f"batch record {path.name} holds a different identity "
                f"({batch.batch_id!r})"
            )
        return batch

    def save(self, batch: PMBatch) -> Path:
        """Persist *batch* as machine-readable JSON; return its path."""

        if not isinstance(batch, PMBatch):
            raise ArtifactValidationError(
                "only a PMBatch can be persisted by the batch store"
            )
        path = self.path_for(batch.batch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(batch.to_json() + "\n", encoding="utf-8")
        return path

    def batches_containing(self, proposal_id: str) -> tuple[PMBatch, ...]:
        """Every stored batch that authorizes *proposal_id*.

        Fail-closed: one well-formed batch identity that cannot be read
        or is invalid refuses the whole lookup instead of being
        silently skipped, so an unreadable batch can never hide a
        proposal from the approval gate.
        """

        if not self.directory.is_dir():
            return ()
        found: list[PMBatch] = []
        for path in sorted(self.directory.glob("*.json")):
            if not BATCH_ID_RE.match(path.stem):
                continue
            batch = self.load(path.stem)
            if proposal_id in batch.proposal_ids:
                found.append(batch)
        return tuple(found)


class PMRuntime:
    """Authoritative PM-side runtime: context -> proposal -> batch -> gate.

    One instance owns one project root.  It is the only place in this
    MVP where a request becomes a proposal, where proposals become an
    approvable batch, and where an implementation handoff is
    authorized; everything else (execution, orchestration,
    interpretation) stays outside this package.
    """

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        store: ProposalStore | None = None,
        batch_store: BatchStore | None = None,
        context: ProjectContext | None = None,
    ) -> None:
        self._root = Path(root) if root is not None else default_project_root()
        self._store = store if store is not None else ProposalStore(self._root)
        self._batch_store = (
            batch_store if batch_store is not None else BatchStore(self._root)
        )
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
    def batch_store(self) -> BatchStore:
        """The durable batch store this runtime writes to."""

        return self._batch_store

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

    def propose_batch(
        self, proposal_ids: Sequence[str]
    ) -> PMBatch:
        """Group stored proposals into a persisted, approvable batch.

        Steps:

        1. validate the membership — a non-empty sequence of
           well-formed proposal identities with no repeat
           (:func:`~pm.batch.normalize_member_ids`);
        2. load the PM-owned project context (fail-closed) and derive
           the deterministic batch identity from project + requirement
           version + member set, independent of member order;
        3. return the existing record unchanged when the same batch was
           proposed before — an approval is never dropped;
        4. otherwise load every member proposal (an unknown identity
           fails closed with
           :class:`~pm.errors.ProposalNotFoundError`, before anything
           is written) and persist the batch.

        Creating a batch records no approval, changes no register file,
        and reaches no backend: it only represents which proposals a
        later batch approval would authorize.
        """

        context = self.context
        member_ids = normalize_member_ids(proposal_ids)
        identity = derive_batch_identity(
            context.project, context.requirement_version, member_ids
        )
        if self._batch_store.exists(identity.batch_id):
            return self._batch_store.load(identity.batch_id)

        batch = PMBatch(
            batch_id=identity.batch_id,
            correlation_id=identity.correlation_id,
            idempotency_key=identity.idempotency_key,
            created_at=_utc_now(),
            project=context.project,
            requirement_version=context.requirement_version,
            proposals=tuple(
                self._store.load(proposal_id) for proposal_id in member_ids
            ),
            approval=None,
        )
        self._batch_store.save(batch)
        return batch

    def get_batch(self, batch_id: str) -> PMBatch:
        """Load a stored batch; unknown identities fail closed."""

        return self._batch_store.load(batch_id)

    def _verify_batch_members(self, batch: PMBatch) -> tuple[PMProposal, ...]:
        """Re-read every member from the store, failing closed on drift.

        Returns the *stored* member proposals.  A member that no longer
        exists raises :class:`~pm.errors.ProposalNotFoundError`; a
        member whose stored content no longer matches the batch record
        raises :class:`PMRuntimeError`.  An inconsistent batch can
        therefore never be approved and never handed off.
        """

        stored_members: list[PMProposal] = []
        for member in batch.proposals:
            stored = self._store.load(member.proposal_id)
            if proposal_content(stored) != proposal_content(member):
                raise PMRuntimeError(
                    f"batch {batch.batch_id} member "
                    f"{member.proposal_id} no longer matches its stored "
                    "proposal; batch refused"
                )
            stored_members.append(stored)
        return tuple(stored_members)

    def approve_batch(
        self, batch_id: str, *, approver: str, note: str = ""
    ) -> PMBatch:
        """Record the explicit PM approval for one stored batch.

        This is a Project-Manager act persisted on the batch record.
        It changes no member proposal's own approval, creates no
        lifecycle state, changes no task state, grants no permission,
        and cannot be overwritten once recorded.

        Before the approval is written the batch is re-validated
        against the proposal store (every member must still exist and
        still match), so an inconsistent batch can never be approved.
        """

        _require_identity(approver, "approver")
        if not isinstance(note, str):
            raise ArtifactValidationError("note must be a string")

        batch = self.get_batch(batch_id)
        if batch.approval is not None:
            raise BatchAlreadyApprovedError(
                f"batch {batch.batch_id} is already approved by "
                f"{batch.approval.approver!r}"
            )
        self._verify_batch_members(batch)
        approved = replace(
            batch,
            approval=ApprovalRecord(
                approver=approver, approved_at=_utc_now(), note=note
            ),
        )
        self._batch_store.save(approved)
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
          reaches an implementation backend;
        - :class:`~pm.errors.BatchNotApprovedError` when the proposal
          belongs to a stored batch that carries no batch approval
          record — no execution or handoff may occur before the batch
          authorizing the proposal is approved.

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
        for batch in self._batch_store.batches_containing(
            proposal.proposal_id
        ):
            if batch.approval is None:
                raise BatchNotApprovedError(
                    f"proposal {proposal.proposal_id} belongs to "
                    f"unapproved batch {batch.batch_id}; implementation "
                    "handoff refused"
                )
        return backend(proposal)

    def handoff_batch(
        self,
        batch_id: str,
        backend: Callable[[PMBatch], Any],
    ) -> Any:
        """The gated batch implementation handoff (fail-closed).

        ``backend`` is an opaque callable supplied by the caller — this
        runtime neither selects nor imports an execution backend, and
        nothing here schedules or orders the batch members (that is
        later MVP work, not this contract).

        Raises before any backend call:

        - :class:`~pm.errors.PMRuntimeError` when ``backend`` is not
          callable, or when a stored member no longer matches the batch
          record;
        - :class:`~pm.errors.BatchNotFoundError` when no batch is
          stored for ``batch_id``;
        - :class:`~pm.errors.ProposalNotFoundError` when a member
          proposal no longer exists;
        - :class:`~pm.errors.BatchNotApprovedError` when the batch
          carries no approval record — an unapproved batch never
          reaches an implementation backend;
        - :class:`~pm.errors.ProposalNotApprovedError` when any member
          proposal carries no approval of its own — a batch approval
          never substitutes for, creates, or waives a member approval.

        On success the backend receives exactly one argument (the
        approved batch) and its raw return value is returned untouched.
        """

        if not callable(backend):
            raise PMRuntimeError(
                "backend must be a callable accepting one PMBatch"
            )
        batch = self.get_batch(batch_id)
        if batch.approval is None:
            raise BatchNotApprovedError(
                f"batch {batch.batch_id} is not approved; implementation "
                "handoff refused"
            )
        stored_members = self._verify_batch_members(batch)
        for stored in stored_members:
            if not stored.is_approved:
                raise ProposalNotApprovedError(
                    f"proposal {stored.proposal_id} of batch "
                    f"{batch.batch_id} is not approved; batch handoff "
                    "refused"
                )
        return backend(batch)
