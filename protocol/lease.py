"""Interrogator AI-Assisted Project Lifecycle Protocol V1.1 — execution
lease and UNKNOWN recovery.

Protocol V1.1 requires that:

- at most one active execution lease exists for a ``task_id`` +
  ``task_version``;
- an expired lease enters reconciliation and is never silently
  replaced;
- ``UNKNOWN`` execution state is representable;
- an ``UNKNOWN`` execution is reconciled with the execution backend
  before any replacement is authorized;
- a replacement execution may only be created after reconciliation
  establishes that the prior execution is absent **and** the PM has
  explicitly authorized the replacement;
- a duplicate execution is never created silently.

Lease and recovery logic lives beside the TASK-002 artifact model and
deliberately depends only on :mod:`protocol.artifacts` plus the standard
library — never on ``router``/JEV or any provider.  Execution backends
participate through the small :class:`ExecutionProbe` interface, so
governance stays independent of provider execution.

The states defined here describe an *execution/lease*, not the V1.1 task
or handoff lifecycles, which remain exactly as defined by
:mod:`protocol.artifacts`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    TaskArtifact,
    _coerce_state,
    _require_identity,
)


# ------------------------------------------------------------------
# Errors
# ------------------------------------------------------------------

class LeaseError(RuntimeError):
    """Base class for execution-lease and recovery failures."""


class DuplicateLeaseError(LeaseError):
    """A second active lease already exists for the task/version."""


class ReconciliationRequiredError(LeaseError):
    """The lease expired or the execution state is UNKNOWN; reconcile first."""


class ReplacementNotAuthorizedError(LeaseError):
    """A replacement lacks confirmed absence of the prior execution or
    explicit PM authorization."""


class DuplicateExecutionError(LeaseError):
    """The requested identity would silently duplicate a known execution."""


# ------------------------------------------------------------------
# Execution/lease states (not V1.1 task or handoff lifecycle states)
# ------------------------------------------------------------------

class ExecutionState(StrEnum):
    """Observed state of an execution.

    ``UNKNOWN`` represents an execution whose state the governance layer
    can no longer confirm without asking the execution backend; it must
    be reconciled before any replacement is authorized.
    """

    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    ABSENT = "ABSENT"
    UNKNOWN = "UNKNOWN"


class LeaseState(StrEnum):
    """Governance state of the lease slot for one task/version."""

    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    AWAITING_REPLACEMENT = "AWAITING_REPLACEMENT"
    RELEASED = "RELEASED"


class ReconciliationOutcome(StrEnum):
    """Result of reconciling an execution with its backend."""

    OBSERVED = "OBSERVED"
    RESULT_COLLECTED = "RESULT_COLLECTED"
    ABSENT = "ABSENT"
    UNRESOLVED = "UNRESOLVED"


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------

def _require_number(value: object, field_name: str) -> None:
    """Reject non-numeric clock values and timestamps."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LeaseError(f"{field_name} must be a number; got {value!r}")


def _require_positive_ttl(ttl: object) -> None:
    """Reject lease terms that do not expire."""

    _require_number(ttl, "ttl")
    if ttl <= 0:  # type: ignore[operator]
        raise LeaseError(f"ttl must be positive; got {ttl!r}")


def _validate_task(task: object) -> None:
    if not isinstance(task, TaskArtifact):
        raise ArtifactValidationError("task must be a TaskArtifact")


def _validate_execution(execution: object) -> None:
    if not isinstance(execution, ExecutionArtifact):
        raise ArtifactValidationError("execution must be an ExecutionArtifact")


# ------------------------------------------------------------------
# Artifacts of the lease/recovery model
# ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """Exclusive governance claim on one execution for a task/version.

    ``acquired_at`` is the clock time at which the current lease term
    began (initial acquisition or the most recent recovery); the term
    ends at ``acquired_at + ttl``.
    """

    lease_id: str
    task_id: str
    task_version: str
    execution: ExecutionArtifact
    acquired_at: float
    ttl: float

    def __post_init__(self) -> None:
        _require_identity(self.lease_id, "lease_id")
        _require_identity(self.task_id, "task_id")
        _require_identity(self.task_version, "task_version")
        _validate_execution(self.execution)
        _require_number(self.acquired_at, "acquired_at")
        _require_positive_ttl(self.ttl)

    @property
    def expires_at(self) -> float:
        """Clock time at which this lease term expires."""
        return self.acquired_at + self.ttl


@dataclass(frozen=True, slots=True)
class BackendObservation:
    """What an execution backend reports about one execution identity."""

    execution_id: str
    state: ExecutionState | str
    result: Any = None
    detail: str = ""

    def __post_init__(self) -> None:
        _require_identity(self.execution_id, "execution_id")
        object.__setattr__(
            self, "state", _coerce_state(self.state, ExecutionState, "state")
        )
        if not isinstance(self.detail, str):
            raise ArtifactValidationError("detail must be a string")


class ExecutionProbe(Protocol):
    """Minimal execution-backend interface used for reconciliation.

    Governance code depends only on this interface, never on a concrete
    provider or execution adapter.
    """

    def observe(self, execution_id: str) -> BackendObservation:
        """Report what the backend currently observes for *execution_id*."""
        ...


@dataclass(frozen=True, slots=True)
class PMAuthorization:
    """Explicit PM authorization permitting one replacement execution.

    Bound to the exact task/version and prior execution it replaces.
    """

    task_id: str
    task_version: str
    execution_id: str
    authorized_by: str
    basis: str = ""

    def __post_init__(self) -> None:
        _require_identity(self.task_id, "task_id")
        _require_identity(self.task_version, "task_version")
        _require_identity(self.execution_id, "execution_id")
        _require_identity(self.authorized_by, "authorized_by")
        if not isinstance(self.basis, str):
            raise ArtifactValidationError("basis must be a string")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Outcome of one reconciliation of a task/version with its backend."""

    task_id: str
    task_version: str
    execution_id: str
    outcome: ReconciliationOutcome
    result: Any = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LeaseStatus:
    """Read-only view of one task/version lease slot."""

    task_id: str
    task_version: str
    lease: ExecutionLease
    lease_state: LeaseState
    execution_state: ExecutionState
    replaced: ExecutionArtifact | None = None
    authorization: PMAuthorization | None = None

    @property
    def requires_reconciliation(self) -> bool:
        """True while the execution must be reconciled with the backend."""
        return self.execution_state is ExecutionState.UNKNOWN

    @property
    def replacement_authorized(self) -> bool:
        """True while explicit PM authorization for replacement is held."""
        return self.authorization is not None


# ------------------------------------------------------------------
# Internal record
# ------------------------------------------------------------------

@dataclass(slots=True)
class _LeaseRecord:
    """Mutable governance record for one task/version lease slot."""

    lease: ExecutionLease
    lease_state: LeaseState
    execution_state: ExecutionState
    used_lease_ids: set[str]
    used_execution_ids: set[str]
    used_idempotency_keys: set[str]
    replaced: ExecutionArtifact | None = None
    authorization: PMAuthorization | None = None


def _new_record(lease: ExecutionLease) -> _LeaseRecord:
    return _LeaseRecord(
        lease=lease,
        lease_state=LeaseState.ACTIVE,
        execution_state=ExecutionState.RUNNING,
        used_lease_ids={lease.lease_id},
        used_execution_ids={lease.execution.execution_id},
        used_idempotency_keys={lease.execution.idempotency_key},
    )


# ------------------------------------------------------------------
# Manager
# ------------------------------------------------------------------

class ExecutionLeaseManager:
    """Enforces the V1.1 execution-lease and UNKNOWN-recovery rules.

    Invariants:

    - At most one lease record exists per ``task_id``/``task_version``,
      and at most one *active* lease per record.
    - An expired lease transitions to ``EXPIRED`` with an ``UNKNOWN``
      execution state; the slot then requires reconciliation and is
      never silently replaced.
    - Reconciliation with the backend recovers a running execution,
      collects a finished execution's result, or establishes that the
      prior execution is absent.
    - A replacement execution is created only through
      :meth:`create_replacement`, which requires both confirmed absence
      and explicit PM authorization, and rejects any identity that
      would duplicate a known execution.

    The clock is injectable so expiry and recovery are deterministic in
    tests.  All state transitions are guarded by a lock so concurrent
    acquisitions cannot both win.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._records: dict[tuple[str, str], _LeaseRecord] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lease lifecycle
    # ------------------------------------------------------------------

    def acquire(
        self,
        task: TaskArtifact,
        execution: ExecutionArtifact,
        *,
        lease_id: str,
        ttl: float,
    ) -> ExecutionLease:
        """Acquire the exclusive lease for *task*'s current version.

        Raises :class:`DuplicateLeaseError` if an active lease already
        exists, :class:`ReconciliationRequiredError` if the previous
        lease expired or the execution state is UNKNOWN, and
        :class:`ReplacementNotAuthorizedError` if a replacement is
        pending PM authorization.
        """

        _validate_task(task)
        _validate_execution(execution)
        _require_identity(lease_id, "lease_id")
        _require_positive_ttl(ttl)

        key = (task.task_id, task.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is not None:
                self._refresh(record, now)
                if record.lease_state is LeaseState.RELEASED:
                    # Slot is free, but a known identity may never be
                    # silently duplicated.
                    self._reject_reused_identity(record, lease_id, execution)
                elif (
                    record.lease_state is LeaseState.EXPIRED
                    or record.execution_state is ExecutionState.UNKNOWN
                ):
                    raise ReconciliationRequiredError(
                        f"lease {record.lease.lease_id!r} for "
                        f"{task.task_id} version {task.task_version} "
                        "expired with an UNKNOWN execution state; the "
                        "execution must be reconciled with the backend "
                        "before any new lease is acquired"
                    )
                elif record.lease_state is LeaseState.AWAITING_REPLACEMENT:
                    raise ReplacementNotAuthorizedError(
                        "the prior execution is absent; a replacement "
                        "requires explicit PM authorization via "
                        "authorize_replacement() and create_replacement()"
                    )
                else:
                    raise DuplicateLeaseError(
                        f"active lease {record.lease.lease_id!r} already "
                        f"exists for {task.task_id} version "
                        f"{task.task_version}"
                    )
            else:
                record = _new_record(
                    ExecutionLease(
                        lease_id=lease_id,
                        task_id=key[0],
                        task_version=key[1],
                        execution=execution,
                        acquired_at=now,
                        ttl=ttl,
                    )
                )
                self._records[key] = record
                return record.lease

            lease = ExecutionLease(
                lease_id=lease_id,
                task_id=key[0],
                task_version=key[1],
                execution=execution,
                acquired_at=now,
                ttl=ttl,
            )
            record.lease = lease
            record.lease_state = LeaseState.ACTIVE
            record.execution_state = ExecutionState.RUNNING
            record.replaced = None
            record.authorization = None
            record.used_lease_ids.add(lease.lease_id)
            record.used_execution_ids.add(execution.execution_id)
            record.used_idempotency_keys.add(execution.idempotency_key)
            return lease

    def release(self, lease: ExecutionLease) -> LeaseStatus:
        """Conclude *lease* after its execution has finished.

        An expired/UNKNOWN lease must be reconciled first; a lease
        awaiting replacement must go through the authorized replacement
        path instead.
        """

        if not isinstance(lease, ExecutionLease):
            raise LeaseError("lease must be an ExecutionLease")

        key = (lease.task_id, lease.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None or record.lease.lease_id != lease.lease_id:
                raise LeaseError(
                    f"lease {lease.lease_id!r} is not the held lease for "
                    f"{lease.task_id} version {lease.task_version}"
                )

            self._refresh(record, now)

            if record.lease_state is LeaseState.RELEASED:
                raise LeaseError(f"lease {lease.lease_id!r} is already released")

            if (
                record.lease_state is LeaseState.EXPIRED
                or record.execution_state is ExecutionState.UNKNOWN
            ):
                raise ReconciliationRequiredError(
                    f"lease {lease.lease_id!r} expired or its execution is "
                    "UNKNOWN; reconcile with the backend before release"
                )

            if record.lease_state is LeaseState.AWAITING_REPLACEMENT:
                raise ReplacementNotAuthorizedError(
                    "the prior execution is absent; release is not the "
                    "replacement path — use authorize_replacement() and "
                    "create_replacement()"
                )

            if record.execution_state is not ExecutionState.RUNNING:
                raise LeaseError(
                    "only a running execution can be released; execution "
                    f"state is {record.execution_state}"
                )

            record.lease_state = LeaseState.RELEASED
            record.execution_state = ExecutionState.FINISHED
            record.authorization = None
            return self._status(key, record)

    def mark_unknown(self, lease: ExecutionLease) -> LeaseStatus:
        """Record that *lease*'s execution state can no longer be confirmed.

        The slot then requires reconciliation with the backend before it
        can be released or replaced.
        """

        if not isinstance(lease, ExecutionLease):
            raise LeaseError("lease must be an ExecutionLease")

        key = (lease.task_id, lease.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None or record.lease.lease_id != lease.lease_id:
                raise LeaseError(
                    f"lease {lease.lease_id!r} is not the held lease for "
                    f"{lease.task_id} version {lease.task_version}"
                )

            self._refresh(record, now)

            if record.lease_state is LeaseState.RELEASED:
                raise LeaseError(f"lease {lease.lease_id!r} is already released")

            record.execution_state = ExecutionState.UNKNOWN
            return self._status(key, record)

    # ------------------------------------------------------------------
    # Reconciliation and replacement
    # ------------------------------------------------------------------

    def reconcile(
        self, task: TaskArtifact, probe: ExecutionProbe
    ) -> ReconciliationReport:
        """Reconcile the task/version's execution with its backend.

        - backend confirms running → the execution is recovered and
          observed under a renewed lease (outcome ``OBSERVED``);
        - backend confirms finished → its result is collected and the
          lease is released (outcome ``RESULT_COLLECTED``);
        - backend confirms absent → the slot awaits PM-authorized
          replacement (outcome ``ABSENT``);
        - backend cannot confirm → the record is untouched and stays
          reconciliation-required (outcome ``UNRESOLVED``).
        """

        _validate_task(task)
        if not callable(getattr(probe, "observe", None)):
            raise LeaseError("probe must provide observe(execution_id)")

        key = (task.task_id, task.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None:
                raise LeaseError(
                    f"no lease record exists for {task.task_id} version "
                    f"{task.task_version}"
                )

            self._refresh(record, now)

            if record.lease_state is LeaseState.RELEASED:
                raise LeaseError(
                    f"lease {record.lease.lease_id!r} is already released; "
                    "nothing to reconcile"
                )

            execution_id = record.lease.execution.execution_id

            try:
                observation = probe.observe(execution_id)
            except Exception as exc:  # backend unreachable: stay unresolved
                return self._report(key, execution_id,
                                    ReconciliationOutcome.UNRESOLVED,
                                    error=f"backend observation failed: {exc}")

            if not isinstance(observation, BackendObservation):
                return self._report(
                    key, execution_id, ReconciliationOutcome.UNRESOLVED,
                    error="probe returned no valid BackendObservation",
                )

            if observation.execution_id != execution_id:
                return self._report(
                    key, execution_id, ReconciliationOutcome.UNRESOLVED,
                    error=(
                        "observation is for execution "
                        f"{observation.execution_id!r}, expected "
                        f"{execution_id!r}"
                    ),
                )

            state = observation.state

            if state is ExecutionState.RUNNING:
                # Recover/observe: the same execution continues under a
                # renewed term of the same lease. No new execution.
                record.lease_state = LeaseState.ACTIVE
                record.execution_state = ExecutionState.RUNNING
                record.authorization = None
                record.lease = replace(record.lease, acquired_at=now)
                return self._report(
                    key, execution_id, ReconciliationOutcome.OBSERVED,
                )

            if state is ExecutionState.FINISHED:
                # Collect the result; the execution is concluded.
                record.lease_state = LeaseState.RELEASED
                record.execution_state = ExecutionState.FINISHED
                record.authorization = None
                return self._report(
                    key, execution_id, ReconciliationOutcome.RESULT_COLLECTED,
                    result=observation.result,
                )

            if state is ExecutionState.ABSENT:
                # Prior execution confirmed absent: replacement now
                # requires explicit PM authorization.
                record.lease_state = LeaseState.AWAITING_REPLACEMENT
                record.execution_state = ExecutionState.ABSENT
                return self._report(
                    key, execution_id, ReconciliationOutcome.ABSENT,
                )

            # UNKNOWN: the backend could not confirm the state.
            record.execution_state = ExecutionState.UNKNOWN
            return self._report(
                key, execution_id, ReconciliationOutcome.UNRESOLVED,
                error=observation.detail or "backend reported UNKNOWN",
            )

    def authorize_replacement(
        self, task: TaskArtifact, authorization: PMAuthorization
    ) -> LeaseStatus:
        """Record the PM's explicit authorization for a replacement.

        Valid only after reconciliation has established that the prior
        execution is absent and only for the exact task/version and
        execution the authorization names.
        """

        _validate_task(task)
        if not isinstance(authorization, PMAuthorization):
            raise LeaseError("authorization must be a PMAuthorization")

        key = (task.task_id, task.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None:
                raise LeaseError(
                    f"no lease record exists for {task.task_id} version "
                    f"{task.task_version}"
                )

            self._refresh(record, now)

            if record.lease_state is LeaseState.RELEASED:
                raise LeaseError(
                    "no prior execution is pending replacement for "
                    f"{task.task_id} version {task.task_version}"
                )

            if (
                record.lease_state is LeaseState.EXPIRED
                or record.execution_state is ExecutionState.UNKNOWN
            ):
                raise ReconciliationRequiredError(
                    "the execution state is UNKNOWN; reconcile with the "
                    "backend before authorizing any replacement"
                )

            if record.execution_state is not ExecutionState.ABSENT:
                raise ReplacementNotAuthorizedError(
                    "reconciliation has not established that the prior "
                    "execution is absent"
                )

            if (
                authorization.task_id != task.task_id
                or authorization.task_version != task.task_version
                or authorization.execution_id
                != record.lease.execution.execution_id
            ):
                raise ReplacementNotAuthorizedError(
                    "authorization does not match this task/version and "
                    "prior execution"
                )

            record.authorization = authorization
            return self._status(key, record)

    def create_replacement(
        self,
        task: TaskArtifact,
        execution: ExecutionArtifact,
        *,
        lease_id: str,
        ttl: float,
    ) -> ExecutionLease:
        """Create the authorized replacement execution for *task*.

        Both Protocol gates are enforced: reconciliation must have
        established that the prior execution is absent, and the PM must
        have authorized this exact replacement. The replacement must
        also carry a fresh lease and execution identity.
        """

        _validate_task(task)
        _validate_execution(execution)
        _require_identity(lease_id, "lease_id")
        _require_positive_ttl(ttl)

        key = (task.task_id, task.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None:
                raise LeaseError(
                    f"no lease record exists for {task.task_id} version "
                    f"{task.task_version}"
                )

            self._refresh(record, now)

            if record.lease_state is LeaseState.RELEASED:
                raise LeaseError(
                    "the prior execution is already concluded; a new "
                    "execution must go through acquire(), not "
                    "create_replacement()"
                )

            if (
                record.lease_state is LeaseState.EXPIRED
                or record.execution_state is ExecutionState.UNKNOWN
            ):
                raise ReconciliationRequiredError(
                    "the prior execution state is UNKNOWN; reconcile it "
                    "with the backend before any replacement"
                )

            if record.execution_state is not ExecutionState.ABSENT:
                raise ReplacementNotAuthorizedError(
                    "reconciliation has not established that the prior "
                    "execution is absent"
                )

            if record.authorization is None:
                raise ReplacementNotAuthorizedError(
                    "replacement requires explicit PM authorization via "
                    "authorize_replacement()"
                )

            if (
                record.authorization.execution_id
                != record.lease.execution.execution_id
            ):
                raise ReplacementNotAuthorizedError(
                    "authorization does not match the prior execution"
                )

            self._reject_reused_identity(record, lease_id, execution)

            lease = ExecutionLease(
                lease_id=lease_id,
                task_id=key[0],
                task_version=key[1],
                execution=execution,
                acquired_at=now,
                ttl=ttl,
            )
            record.replaced = record.lease.execution
            record.lease = lease
            record.lease_state = LeaseState.ACTIVE
            record.execution_state = ExecutionState.RUNNING
            record.authorization = None  # consumed
            record.used_lease_ids.add(lease.lease_id)
            record.used_execution_ids.add(execution.execution_id)
            record.used_idempotency_keys.add(execution.idempotency_key)
            return lease

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def status_for(self, task: TaskArtifact) -> LeaseStatus | None:
        """Return the current status of *task*'s lease slot, if any."""

        _validate_task(task)
        key = (task.task_id, task.task_version)
        now = self._clock()

        with self._lock:
            record = self._records.get(key)
            if record is None:
                return None
            self._refresh(record, now)
            return self._status(key, record)

    # ------------------------------------------------------------------
    # Internal helpers (called with the lock held)
    # ------------------------------------------------------------------

    def _refresh(self, record: _LeaseRecord, now: float) -> None:
        """Lazily transition an expired active lease into reconciliation."""

        if (
            record.lease_state is LeaseState.ACTIVE
            and now >= record.lease.expires_at
        ):
            record.lease_state = LeaseState.EXPIRED
            record.execution_state = ExecutionState.UNKNOWN

    def _reject_reused_identity(
        self,
        record: _LeaseRecord,
        lease_id: str,
        execution: ExecutionArtifact,
    ) -> None:
        """Refuse identities that would silently duplicate known work."""

        if lease_id in record.used_lease_ids:
            raise LeaseError(
                f"lease_id {lease_id!r} has already been used for this "
                "task/version"
            )
        if execution.execution_id in record.used_execution_ids:
            raise DuplicateExecutionError(
                f"execution_id {execution.execution_id!r} already exists "
                "for this task/version; a duplicate execution is never "
                "created silently"
            )
        if execution.idempotency_key in record.used_idempotency_keys:
            raise DuplicateExecutionError(
                f"idempotency_key {execution.idempotency_key!r} already "
                "exists for this task/version; a duplicate execution is "
                "never created silently"
            )

    @staticmethod
    def _report(
        key: tuple[str, str],
        execution_id: str,
        outcome: ReconciliationOutcome,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> ReconciliationReport:
        return ReconciliationReport(
            task_id=key[0],
            task_version=key[1],
            execution_id=execution_id,
            outcome=outcome,
            result=result,
            error=error,
        )

    @staticmethod
    def _status(key: tuple[str, str], record: _LeaseRecord) -> LeaseStatus:
        return LeaseStatus(
            task_id=key[0],
            task_version=key[1],
            lease=record.lease,
            lease_state=record.lease_state,
            execution_state=record.execution_state,
            replaced=record.replaced,
            authorization=record.authorization,
        )
