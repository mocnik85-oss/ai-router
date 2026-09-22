"""Tests for protocol.lease — Protocol V1.1 execution lease and UNKNOWN recovery.

Offline only: no network, model, or provider calls.
"""

from __future__ import annotations

import ast
import inspect
import threading
import unittest
from pathlib import Path

import protocol.lease as lease_module
from protocol.artifacts import ArtifactValidationError, ExecutionArtifact, TaskArtifact
from protocol.lease import (
    BackendObservation,
    DuplicateExecutionError,
    DuplicateLeaseError,
    ExecutionLease,
    ExecutionLeaseManager,
    ExecutionState,
    LeaseError,
    LeaseState,
    PMAuthorization,
    ReconciliationOutcome,
    ReconciliationRequiredError,
    ReplacementNotAuthorizedError,
)

TTL = 30.0
START = 1000.0

LEASE_ID = "LEASE-TASK-003-001"
LEASE_ID_2 = "LEASE-TASK-003-002"

# Exact V1.1-adjacent execution/lease state definitions (these are NOT
# task or handoff lifecycle states, which stay exactly as V1.1 defines
# them in protocol.artifacts).
EXECUTION_STATE_NAMES = ("RUNNING", "FINISHED", "ABSENT", "UNKNOWN")
LEASE_STATE_NAMES = ("ACTIVE", "EXPIRED", "AWAITING_REPLACEMENT", "RELEASED")
OUTCOME_NAMES = (
    "OBSERVED",
    "RESULT_COLLECTED",
    "ABSENT",
    "UNRESOLVED",
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _task(task_id: str = "TASK-003", task_version: str = "1") -> TaskArtifact:
    return TaskArtifact(
        task_id=task_id, task_version=task_version, requirement_version="v1.1"
    )


def _execution(
    execution_id: str = "EXEC-TASK-003-001",
    idempotency_key: str = "TASK-003-v1-exec-001",
) -> ExecutionArtifact:
    return ExecutionArtifact(
        execution_id=execution_id,
        correlation_id="CORR-TASK-003-001",
        idempotency_key=idempotency_key,
    )


class FakeClock:
    """Deterministic monotonic clock for lease expiry tests."""

    def __init__(self, start: float = START) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _manager() -> tuple[ExecutionLeaseManager, FakeClock]:
    clock = FakeClock()
    return ExecutionLeaseManager(clock=clock), clock


def _probe(
    state: ExecutionState | str,
    *,
    execution_id: str | None = None,
    result=None,
    detail: str = "",
):
    """Scripted execution backend: reports *state* for whatever is asked."""

    class _ScriptedProbe:
        def observe(self, observed_id: str) -> BackendObservation:
            return BackendObservation(
                execution_id=execution_id or observed_id,
                state=state,
                result=result,
                detail=detail,
            )

    return _ScriptedProbe()


def _failing_probe(message: str = "backend down"):
    """Execution backend that raises instead of answering."""

    class _FailingProbe:
        def observe(self, observed_id: str) -> BackendObservation:
            raise RuntimeError(message)

    return _FailingProbe()


def _acquire(mgr: ExecutionLeaseManager) -> ExecutionLease:
    return mgr.acquire(_task(), _execution(), lease_id=LEASE_ID, ttl=TTL)


def _expire(mgr: ExecutionLeaseManager, clock: FakeClock) -> ExecutionLease:
    lease = _acquire(mgr)
    clock.advance(TTL)
    return lease


def _reconcile_absent(mgr: ExecutionLeaseManager) -> None:
    report = mgr.reconcile(_task(), _probe(ExecutionState.ABSENT))
    assert report.outcome is ReconciliationOutcome.ABSENT


# ------------------------------------------------------------------
# State representation
# ------------------------------------------------------------------

class ExecutionStateTests(unittest.TestCase):
    """UNKNOWN execution state must be representable."""

    def test_execution_states_defined_exactly(self) -> None:
        self.assertEqual(
            tuple(m.name for m in ExecutionState), EXECUTION_STATE_NAMES
        )

    def test_values_equal_names(self) -> None:
        for member in ExecutionState:
            with self.subTest(state=member.name):
                self.assertEqual(member.value, member.name)

    def test_serialises_as_protocol_string(self) -> None:
        self.assertEqual(str(ExecutionState.UNKNOWN), "UNKNOWN")
        self.assertEqual(ExecutionState("ABSENT"), ExecutionState.ABSENT)

    def test_invented_execution_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionState("INVENTED")


class LeaseStateTests(unittest.TestCase):
    """Lease slot states covering active, expired, and replacement."""

    def test_lease_states_defined_exactly(self) -> None:
        self.assertEqual(tuple(m.name for m in LeaseState), LEASE_STATE_NAMES)

    def test_invented_lease_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LeaseState("INVENTED")


class ReconciliationOutcomeTests(unittest.TestCase):
    """Reconciliation must express every recovery path."""

    def test_outcomes_defined_exactly(self) -> None:
        self.assertEqual(
            tuple(m.name for m in ReconciliationOutcome), OUTCOME_NAMES
        )


# ------------------------------------------------------------------
# Artifact validation
# ------------------------------------------------------------------

class ExecutionLeaseTests(unittest.TestCase):
    """Lease identity, execution binding, and term validation."""

    def test_lease_fields_representable(self) -> None:
        lease = ExecutionLease(
            lease_id=LEASE_ID,
            task_id="TASK-003",
            task_version="1",
            execution=_execution(),
            acquired_at=START,
            ttl=TTL,
        )
        self.assertEqual(lease.lease_id, LEASE_ID)
        self.assertEqual(lease.task_id, "TASK-003")
        self.assertEqual(lease.task_version, "1")
        self.assertEqual(lease.execution.execution_id, "EXEC-TASK-003-001")
        self.assertEqual(lease.expires_at, START + TTL)

    def test_missing_lease_identity_rejected(self) -> None:
        for bad in ("", "   ", None):
            with self.subTest(lease_id=bad):
                with self.assertRaises(ArtifactValidationError):
                    ExecutionLease(
                        lease_id=bad,
                        task_id="TASK-003",
                        task_version="1",
                        execution=_execution(),
                        acquired_at=START,
                        ttl=TTL,
                    )

    def test_non_positive_or_non_numeric_ttl_rejected(self) -> None:
        for bad in (0, -1, "30", None, True):
            with self.subTest(ttl=bad):
                with self.assertRaises(LeaseError):
                    ExecutionLease(
                        lease_id=LEASE_ID,
                        task_id="TASK-003",
                        task_version="1",
                        execution=_execution(),
                        acquired_at=START,
                        ttl=bad,  # type: ignore[arg-type]
                    )


class BackendObservationTests(unittest.TestCase):
    """Backend observations must carry a valid execution state."""

    def test_blank_execution_id_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            BackendObservation(execution_id="", state=ExecutionState.RUNNING)

    def test_invented_state_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            BackendObservation(execution_id="EXEC-1", state="LOST")

    def test_non_string_detail_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            BackendObservation(
                execution_id="EXEC-1",
                state=ExecutionState.UNKNOWN,
                detail=None,  # type: ignore[arg-type]
            )


class PMAuthorizationTests(unittest.TestCase):
    """PM replacement authorization identity and binding."""

    def test_authorization_fields_representable(self) -> None:
        auth = PMAuthorization(
            task_id="TASK-003",
            task_version="1",
            execution_id="EXEC-TASK-003-001",
            authorized_by="Project Manager",
            basis="prior execution confirmed absent",
        )
        self.assertEqual(auth.authorized_by, "Project Manager")
        self.assertEqual(auth.basis, "prior execution confirmed absent")

    def test_basis_defaults_empty(self) -> None:
        auth = PMAuthorization(
            task_id="TASK-003",
            task_version="1",
            execution_id="EXEC-TASK-003-001",
            authorized_by="Project Manager",
        )
        self.assertEqual(auth.basis, "")

    def test_invalid_required_identity_rejected(self) -> None:
        fields = ("task_id", "task_version", "execution_id", "authorized_by")
        for field in fields:
            for bad in ("", "   ", None):
                with self.subTest(field=field, value=bad):
                    kwargs = dict(
                        task_id="TASK-003",
                        task_version="1",
                        execution_id="EXEC-TASK-003-001",
                        authorized_by="Project Manager",
                    )
                    kwargs[field] = bad
                    with self.assertRaises(ArtifactValidationError):
                        PMAuthorization(**kwargs)

    def test_non_string_basis_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            PMAuthorization(
                task_id="TASK-003",
                task_version="1",
                execution_id="EXEC-TASK-003-001",
                authorized_by="Project Manager",
                basis=None,  # type: ignore[arg-type]
            )


# ------------------------------------------------------------------
# Acquisition
# ------------------------------------------------------------------

class LeaseAcquisitionTests(unittest.TestCase):
    """Normal acquisition of an execution lease."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_acquire_returns_active_lease(self) -> None:
        lease = _acquire(self.mgr)
        self.assertEqual(lease.lease_id, LEASE_ID)
        self.assertEqual(lease.task_id, "TASK-003")
        self.assertEqual(lease.task_version, "1")
        self.assertEqual(lease.execution.execution_id, "EXEC-TASK-003-001")
        self.assertEqual(lease.acquired_at, START)
        self.assertEqual(lease.expires_at, START + TTL)

    def test_status_reports_active_running_execution(self) -> None:
        lease = _acquire(self.mgr)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertEqual(status.lease, lease)
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertIs(status.execution_state, ExecutionState.RUNNING)
        self.assertFalse(status.requires_reconciliation)
        self.assertFalse(status.replacement_authorized)
        self.assertIsNone(status.replaced)
        self.assertIsNone(status.authorization)

    def test_unknown_task_has_no_status(self) -> None:
        self.assertIsNone(self.mgr.status_for(_task()))

    def test_acquire_rejects_invalid_ttl(self) -> None:
        for bad in (0, -1, "30", None, True):
            with self.subTest(ttl=bad):
                with self.assertRaises(LeaseError):
                    self.mgr.acquire(
                        _task(),
                        _execution(),
                        lease_id=LEASE_ID,
                        ttl=bad,  # type: ignore[arg-type]
                    )

    def test_acquire_rejects_missing_lease_identity(self) -> None:
        for bad in ("", "   ", None):
            with self.subTest(lease_id=bad):
                with self.assertRaises(ArtifactValidationError):
                    self.mgr.acquire(
                        _task(), _execution(), lease_id=bad, ttl=TTL
                    )

    def test_acquire_rejects_wrong_artifact_types(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            self.mgr.acquire(
                "TASK-003",  # type: ignore[arg-type]
                _execution(),
                lease_id=LEASE_ID,
                ttl=TTL,
            )
        with self.assertRaises(ArtifactValidationError):
            self.mgr.acquire(
                _task(),
                "EXEC-TASK-003-001",  # type: ignore[arg-type]
                lease_id=LEASE_ID,
                ttl=TTL,
            )

    def test_task_versions_have_independent_leases(self) -> None:
        first = self.mgr.acquire(
            _task(task_version="1"),
            _execution(),
            lease_id=LEASE_ID,
            ttl=TTL,
        )
        second = self.mgr.acquire(
            _task(task_version="2"),
            _execution(),
            lease_id="LEASE-TASK-003-v2-001",
            ttl=TTL,
        )
        self.assertNotEqual(first.lease_id, second.lease_id)
        status_v1 = self.mgr.status_for(_task(task_version="1"))
        status_v2 = self.mgr.status_for(_task(task_version="2"))
        assert status_v1 is not None and status_v2 is not None
        self.assertIs(status_v1.lease_state, LeaseState.ACTIVE)
        self.assertIs(status_v2.lease_state, LeaseState.ACTIVE)


# ------------------------------------------------------------------
# Duplicate rejection
# ------------------------------------------------------------------

class DuplicateLeaseTests(unittest.TestCase):
    """At most one active execution lease per task/version."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_second_active_lease_rejected(self) -> None:
        first = _acquire(self.mgr)
        with self.assertRaises(DuplicateLeaseError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertEqual(status.lease, first)
        self.assertIs(status.lease_state, LeaseState.ACTIVE)

    def test_second_lease_rejected_for_same_execution_identity(self) -> None:
        _acquire(self.mgr)
        with self.assertRaises(DuplicateLeaseError):
            self.mgr.acquire(
                _task(), _execution(), lease_id=LEASE_ID_2, ttl=TTL
            )

    def test_concurrent_acquisition_yields_single_active_lease(self) -> None:
        barrier = threading.Barrier(2)
        acquired: list[ExecutionLease] = []
        rejected: list[DuplicateLeaseError] = []

        def worker(index: int) -> None:
            try:
                barrier.wait()
                acquired.append(
                    self.mgr.acquire(
                        _task(),
                        _execution(
                            execution_id=f"EXEC-TASK-003-C{index}",
                            idempotency_key=f"TASK-003-v1-exec-c{index}",
                        ),
                        lease_id=f"LEASE-TASK-003-C{index}",
                        ttl=TTL,
                    )
                )
            except DuplicateLeaseError as exc:
                rejected.append(exc)

        threads = [
            threading.Thread(target=worker, args=(i,)) for i in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(acquired), 1)
        self.assertEqual(len(rejected), 1)


# ------------------------------------------------------------------
# Expiry
# ------------------------------------------------------------------

class ExpiryTests(unittest.TestCase):
    """Expired leases enter reconciliation instead of being replaced."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_expired_lease_enters_reconciliation(self) -> None:
        lease = _acquire(self.mgr)
        self.clock.advance(TTL)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.EXPIRED)
        self.assertIs(status.execution_state, ExecutionState.UNKNOWN)
        self.assertTrue(status.requires_reconciliation)
        self.assertEqual(status.lease, lease)

    def test_expired_lease_is_not_silently_replaced(self) -> None:
        lease = _acquire(self.mgr)
        self.clock.advance(TTL)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.EXPIRED)
        self.assertEqual(status.lease, lease)
        self.assertTrue(status.requires_reconciliation)

    def test_release_of_expired_lease_requires_reconciliation(self) -> None:
        lease = _acquire(self.mgr)
        self.clock.advance(TTL)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.release(lease)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.EXPIRED)

    def test_status_before_expiry_is_active(self) -> None:
        _acquire(self.mgr)
        self.clock.advance(TTL - 1)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertIs(status.execution_state, ExecutionState.RUNNING)


# ------------------------------------------------------------------
# UNKNOWN execution handling and reconciliation
# ------------------------------------------------------------------

class UnknownExecutionTests(unittest.TestCase):
    """UNKNOWN executions are represented and reconciled with the backend."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_mark_unknown_represents_unknown_execution_state(self) -> None:
        lease = _acquire(self.mgr)
        status = self.mgr.mark_unknown(lease)
        self.assertIs(status.execution_state, ExecutionState.UNKNOWN)
        self.assertTrue(status.requires_reconciliation)
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertEqual(str(status.execution_state), "UNKNOWN")

    def test_acquire_blocked_while_execution_unknown(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )

    def test_reconcile_running_recovers_observed_execution(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)

        report = self.mgr.reconcile(_task(), _probe(ExecutionState.RUNNING))

        self.assertIs(report.outcome, ReconciliationOutcome.OBSERVED)
        self.assertEqual(report.execution_id, lease.execution.execution_id)
        self.assertIsNone(report.error)

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertIs(status.execution_state, ExecutionState.RUNNING)
        self.assertFalse(status.requires_reconciliation)
        self.assertEqual(status.lease.lease_id, lease.lease_id)
        self.assertEqual(
            status.lease.execution.execution_id,
            lease.execution.execution_id,
        )
        # The execution was recovered, not replaced: the slot is still held.
        with self.assertRaises(DuplicateLeaseError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )

    def test_reconcile_finished_collects_result(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)

        report = self.mgr.reconcile(
            _task(),
            _probe(ExecutionState.FINISHED, result={"exit_code": 0}),
        )

        self.assertIs(
            report.outcome, ReconciliationOutcome.RESULT_COLLECTED
        )
        self.assertEqual(report.result, {"exit_code": 0})

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.RELEASED)
        self.assertIs(status.execution_state, ExecutionState.FINISHED)
        self.assertFalse(status.requires_reconciliation)

        # The slot is free for a genuinely new execution.
        next_lease = self.mgr.acquire(
            _task(),
            _execution(
                execution_id="EXEC-TASK-003-002",
                idempotency_key="TASK-003-v1-exec-002",
            ),
            lease_id=LEASE_ID_2,
            ttl=TTL,
        )
        self.assertEqual(
            next_lease.execution.execution_id, "EXEC-TASK-003-002"
        )

    def test_reconcile_unknown_stays_unresolved(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)

        report = self.mgr.reconcile(
            _task(),
            _probe(ExecutionState.UNKNOWN, detail="backend ambiguous"),
        )

        self.assertIs(report.outcome, ReconciliationOutcome.UNRESOLVED)
        self.assertEqual(report.error, "backend ambiguous")

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertTrue(status.requires_reconciliation)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )

    def test_probe_failure_leaves_state_unresolved(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)

        report = self.mgr.reconcile(_task(), _failing_probe("backend down"))

        self.assertIs(report.outcome, ReconciliationOutcome.UNRESOLVED)
        assert report.error is not None
        self.assertIn("backend down", report.error)

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.execution_state, ExecutionState.UNKNOWN)
        self.assertTrue(status.requires_reconciliation)

    def test_observation_for_wrong_execution_is_unresolved(self) -> None:
        _acquire(self.mgr)
        self.mgr.mark_unknown(_current_lease(self.mgr))

        report = self.mgr.reconcile(
            _task(),
            _probe(ExecutionState.FINISHED, execution_id="EXEC-OTHER"),
        )

        self.assertIs(report.outcome, ReconciliationOutcome.UNRESOLVED)
        assert report.error is not None
        self.assertIn("EXEC-OTHER", report.error)

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertTrue(status.requires_reconciliation)

    def test_probe_must_expose_observe(self) -> None:
        _acquire(self.mgr)
        with self.assertRaises(LeaseError):
            self.mgr.reconcile(_task(), object())  # type: ignore[arg-type]

    def test_reconcile_requires_existing_record(self) -> None:
        with self.assertRaises(LeaseError):
            self.mgr.reconcile(_task(), _probe(ExecutionState.RUNNING))

    def test_reconcile_after_release_rejected(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.release(lease)
        with self.assertRaises(LeaseError):
            self.mgr.reconcile(_task(), _probe(ExecutionState.RUNNING))


def _current_lease(mgr: ExecutionLeaseManager) -> ExecutionLease:
    status = mgr.status_for(_task())
    assert status is not None
    return status.lease


# ------------------------------------------------------------------
# Expired-lease recovery paths
# ------------------------------------------------------------------

class ExpiryRecoveryTests(unittest.TestCase):
    """Reconciliation of an expired lease recovers or collects — it never
    silently authorizes a replacement."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_expired_execution_running_is_recovered_and_renewed(self) -> None:
        lease = _expire(self.mgr, self.clock)

        report = self.mgr.reconcile(_task(), _probe(ExecutionState.RUNNING))

        self.assertIs(report.outcome, ReconciliationOutcome.OBSERVED)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertIs(status.execution_state, ExecutionState.RUNNING)
        self.assertFalse(status.requires_reconciliation)
        self.assertEqual(status.lease.lease_id, lease.lease_id)
        # The recovered lease term was renewed from the observation time.
        self.assertEqual(status.lease.expires_at, self.clock.now + TTL)

    def test_expired_execution_finished_collects_result(self) -> None:
        _expire(self.mgr, self.clock)

        report = self.mgr.reconcile(
            _task(),
            _probe(ExecutionState.FINISHED, result={"exit_code": 1}),
        )

        self.assertIs(
            report.outcome, ReconciliationOutcome.RESULT_COLLECTED
        )
        self.assertEqual(report.result, {"exit_code": 1})
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.RELEASED)
        self.assertIs(status.execution_state, ExecutionState.FINISHED)

    def test_expired_execution_absent_blocks_replacement(self) -> None:
        _expire(self.mgr, self.clock)

        report = self.mgr.reconcile(_task(), _probe(ExecutionState.ABSENT))

        self.assertIs(report.outcome, ReconciliationOutcome.ABSENT)
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.AWAITING_REPLACEMENT)
        self.assertIs(status.execution_state, ExecutionState.ABSENT)
        self.assertFalse(status.requires_reconciliation)
        self.assertFalse(status.replacement_authorized)
        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )


# ------------------------------------------------------------------
# Replacement authorization
# ------------------------------------------------------------------

class ReplacementTests(unittest.TestCase):
    """Replacement requires reconciliation-confirmed absence AND explicit
    PM authorization, and can never duplicate an execution."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()
        self.prior = _acquire(self.mgr)

    def _create(self):
        return self.mgr.create_replacement(
            _task(),
            _execution(
                execution_id="EXEC-TASK-003-002",
                idempotency_key="TASK-003-v1-exec-002",
            ),
            lease_id=LEASE_ID_2,
            ttl=TTL,
        )

    def _auth(self, **overrides) -> PMAuthorization:
        kwargs = dict(
            task_id="TASK-003",
            task_version="1",
            execution_id="EXEC-TASK-003-001",
            authorized_by="Project Manager",
            basis="prior execution confirmed absent by backend",
        )
        kwargs.update(overrides)
        return PMAuthorization(**kwargs)

    def test_replacement_blocked_before_reconciliation(self) -> None:
        self.clock.advance(TTL)
        with self.assertRaises(ReconciliationRequiredError):
            self._create()
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.EXPIRED)
        self.assertEqual(status.lease, self.prior)

    def test_authorization_before_absence_rejected(self) -> None:
        self.clock.advance(TTL)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.authorize_replacement(_task(), self._auth())
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIsNone(status.authorization)
        with self.assertRaises(ReconciliationRequiredError):
            self._create()

    def test_authorization_on_running_execution_rejected(self) -> None:
        # Lease still active: reconciliation has not established absence.
        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.authorize_replacement(_task(), self._auth())
        with self.assertRaises(ReplacementNotAuthorizedError):
            self._create()

    def test_replacement_requires_pm_authorization_after_absence(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)

        # Gate 1 (absence) satisfied; gate 2 (PM authorization) missing.
        with self.assertRaises(ReplacementNotAuthorizedError):
            self._create()

        status = self.mgr.authorize_replacement(_task(), self._auth())
        self.assertTrue(status.replacement_authorized)
        assert status.authorization is not None
        self.assertEqual(status.authorization.authorized_by, "Project Manager")

        lease = self._create()
        self.assertEqual(lease.lease_id, LEASE_ID_2)
        self.assertEqual(lease.execution.execution_id, "EXEC-TASK-003-002")

    def test_authorization_for_mismatched_binding_rejected(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)

        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.authorize_replacement(
                _task(), self._auth(task_id="TASK-999")
            )
        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.authorize_replacement(
                _task(), self._auth(execution_id="EXEC-OTHER")
            )
        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIsNone(status.authorization)
        with self.assertRaises(ReplacementNotAuthorizedError):
            self._create()

    def test_authorize_and_create_require_existing_record(self) -> None:
        with self.assertRaises(LeaseError):
            self.mgr.authorize_replacement(_task(), self._auth())
        with self.assertRaises(LeaseError):
            self.mgr.create_replacement(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )

    def test_authorized_replacement_creates_single_new_lease(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)
        self.mgr.authorize_replacement(_task(), self._auth())

        lease = self._create()

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIs(status.lease_state, LeaseState.ACTIVE)
        self.assertIs(status.execution_state, ExecutionState.RUNNING)
        self.assertEqual(status.lease, lease)
        self.assertEqual(status.replaced, self.prior.execution)
        self.assertIsNone(status.authorization)  # consumed

        # Still exactly one active lease for the task/version.
        with self.assertRaises(DuplicateLeaseError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-003",
                    idempotency_key="TASK-003-v1-exec-003",
                ),
                lease_id="LEASE-TASK-003-003",
                ttl=TTL,
            )
        # The superseded lease can no longer release the new one.
        with self.assertRaises(LeaseError):
            self.mgr.release(self.prior)
        # The replacement holder can release normally.
        released = self.mgr.release(lease)
        self.assertIs(released.lease_state, LeaseState.RELEASED)
        self.assertIs(released.execution_state, ExecutionState.FINISHED)

    def test_replacement_never_reuses_a_known_identity(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)
        self.mgr.authorize_replacement(_task(), self._auth())

        # Same execution_id as the prior execution.
        with self.assertRaises(DuplicateExecutionError):
            self.mgr.create_replacement(
                _task(),
                _execution(),  # prior identity
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )
        # Same idempotency_key as the prior execution.
        with self.assertRaises(DuplicateExecutionError):
            self.mgr.create_replacement(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-001",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )
        # Already-used lease identity.
        with self.assertRaises(LeaseError):
            self.mgr.create_replacement(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID,  # prior lease identity
                ttl=TTL,
            )
        # A genuinely fresh identity is accepted.
        lease = self._create()
        self.assertEqual(lease.execution.execution_id, "EXEC-TASK-003-002")

    def test_second_replacement_rejected_after_creation(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)
        self.mgr.authorize_replacement(_task(), self._auth())
        self._create()
        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.create_replacement(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-003",
                    idempotency_key="TASK-003-v1-exec-003",
                ),
                lease_id="LEASE-TASK-003-003",
                ttl=TTL,
            )

    def test_authorization_invalidated_if_execution_observed_running(
        self,
    ) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)
        self.mgr.authorize_replacement(_task(), self._auth())

        # The backend later reports the execution is still running.
        report = self.mgr.reconcile(_task(), _probe(ExecutionState.RUNNING))
        self.assertIs(report.outcome, ReconciliationOutcome.OBSERVED)

        status = self.mgr.status_for(_task())
        assert status is not None
        self.assertIsNone(status.authorization)
        with self.assertRaises(ReplacementNotAuthorizedError):
            self._create()

    def test_release_of_absent_lease_requires_replacement_path(self) -> None:
        self.clock.advance(TTL)
        _reconcile_absent(self.mgr)
        # Releasing must not silently bypass the authorization gate.
        with self.assertRaises(ReplacementNotAuthorizedError):
            self.mgr.release(self.prior)

    def test_create_replacement_after_release_rejected(self) -> None:
        self.mgr.release(self.prior)
        with self.assertRaises(LeaseError):
            self._create()


# ------------------------------------------------------------------
# Release and UNKNOWN marking guards
# ------------------------------------------------------------------

class ReleaseTests(unittest.TestCase):
    """Normal completion, stale-lease, and guarded release paths."""

    def setUp(self) -> None:
        self.mgr, self.clock = _manager()

    def test_release_concludes_active_lease(self) -> None:
        lease = _acquire(self.mgr)
        status = self.mgr.release(lease)
        self.assertIs(status.lease_state, LeaseState.RELEASED)
        self.assertIs(status.execution_state, ExecutionState.FINISHED)
        self.assertFalse(status.requires_reconciliation)

        # The slot accepts a genuinely new execution afterwards.
        next_lease = self.mgr.acquire(
            _task(),
            _execution(
                execution_id="EXEC-TASK-003-002",
                idempotency_key="TASK-003-v1-exec-002",
            ),
            lease_id=LEASE_ID_2,
            ttl=TTL,
        )
        self.assertEqual(next_lease.lease_id, LEASE_ID_2)

    def test_new_execution_after_release_never_reuses_identity(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.release(lease)

        # Same execution identity (new lease id): rejected.
        with self.assertRaises(DuplicateExecutionError):
            self.mgr.acquire(
                _task(), _execution(), lease_id=LEASE_ID_2, ttl=TTL
            )
        # Old lease identity (new execution): rejected.
        with self.assertRaises(LeaseError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-002",
                    idempotency_key="TASK-003-v1-exec-002",
                ),
                lease_id=LEASE_ID,
                ttl=TTL,
            )
        # Same idempotency key (fresh ids elsewhere): rejected.
        with self.assertRaises(DuplicateExecutionError):
            self.mgr.acquire(
                _task(),
                _execution(
                    execution_id="EXEC-TASK-003-003",
                    idempotency_key="TASK-003-v1-exec-001",
                ),
                lease_id=LEASE_ID_2,
                ttl=TTL,
            )
        # Fresh identity: accepted.
        self.mgr.acquire(
            _task(),
            _execution(
                execution_id="EXEC-TASK-003-002",
                idempotency_key="TASK-003-v1-exec-002",
            ),
            lease_id=LEASE_ID_2,
            ttl=TTL,
        )

    def test_double_release_rejected(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.release(lease)
        with self.assertRaises(LeaseError):
            self.mgr.release(lease)

    def test_release_with_stale_lease_rejected(self) -> None:
        _acquire(self.mgr)
        self.mgr.release(_current_lease(self.mgr))
        self.mgr.acquire(
            _task(),
            _execution(
                execution_id="EXEC-TASK-003-002",
                idempotency_key="TASK-003-v1-exec-002",
            ),
            lease_id=LEASE_ID_2,
            ttl=TTL,
        )
        stale = ExecutionLease(
            lease_id=LEASE_ID,
            task_id="TASK-003",
            task_version="1",
            execution=_execution(),
            acquired_at=START,
            ttl=TTL,
        )
        with self.assertRaises(LeaseError):
            self.mgr.release(stale)

    def test_release_rejects_wrong_type(self) -> None:
        with self.assertRaises(LeaseError):
            self.mgr.release(LEASE_ID)  # type: ignore[arg-type]

    def test_release_while_unknown_requires_reconciliation(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.mark_unknown(lease)
        with self.assertRaises(ReconciliationRequiredError):
            self.mgr.release(lease)

    def test_mark_unknown_rejects_stale_and_released_leases(self) -> None:
        lease = _acquire(self.mgr)
        self.mgr.release(lease)
        with self.assertRaises(LeaseError):
            self.mgr.mark_unknown(lease)

        stale = ExecutionLease(
            lease_id="LEASE-STALE",
            task_id="TASK-003",
            task_version="1",
            execution=_execution(),
            acquired_at=START,
            ttl=TTL,
        )
        with self.assertRaises(LeaseError):
            self.mgr.mark_unknown(stale)


# ------------------------------------------------------------------
# Architectural independence
# ------------------------------------------------------------------

class GovernanceIndependenceTests(unittest.TestCase):
    """Lease/recovery logic must stay independent of provider execution."""

    def test_lease_module_imports_governance_only(self) -> None:
        source = Path(
            inspect.getsourcefile(lease_module) or ""
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")

        roots = {name.split(".")[0] for name in imported}
        allowed = {
            "__future__",
            "threading",
            "time",
            "collections",
            "dataclasses",
            "enum",
            "typing",
            "protocol",
        }
        self.assertTrue(
            roots <= allowed,
            f"protocol.lease imports outside governance scope: "
            f"{sorted(roots - allowed)}",
        )
        # It must reuse the TASK-002 artifact model, not a duplicate one.
        self.assertIn("protocol.artifacts", imported)


if __name__ == "__main__":
    unittest.main()
