"""Tests for protocol.artifacts — Protocol V1.1 authoritative artifact model.

Offline only: no network, model, or provider calls.
"""

from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError, replace

from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    HandoffArtifact,
    HandoffState,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
)


# Exact V1.1 definitions — order included.
TASK_STATE_NAMES = (
    "PENDING",
    "READY",
    "IMPLEMENTING",
    "VERIFYING",
    "AUDITING",
    "COMPLETED",
    "BLOCKED",
    "NEEDS_USER",
    "CANCELLED",
    "OBSOLETE",
)

HANDOFF_STATE_NAMES = (
    "PENDING",
    "DISPATCHED",
    "IN_PROGRESS",
    "RESULT_READY",
    "PROCESSED",
    "SUPERSEDED",
)

INVALID_IDENTITIES = ("", "   ", None)


class TaskStateTests(unittest.TestCase):
    """Task lifecycle states must match V1.1 exactly."""

    def test_task_states_exactly_match_v1_1(self) -> None:
        self.assertEqual(tuple(m.name for m in TaskState), TASK_STATE_NAMES)

    def test_task_state_count_has_no_extras(self) -> None:
        self.assertEqual(len(TaskState), len(TASK_STATE_NAMES))

    def test_values_equal_names(self) -> None:
        for member in TaskState:
            with self.subTest(state=member.name):
                self.assertEqual(member.value, member.name)

    def test_serialises_as_protocol_string(self) -> None:
        self.assertEqual(str(TaskState.IMPLEMENTING), "IMPLEMENTING")
        self.assertEqual(TaskState("AUDITING"), TaskState.AUDITING)

    def test_invented_task_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TaskState("INVENTED")


class HandoffStateTests(unittest.TestCase):
    """Handoff lifecycle states must match V1.1 exactly."""

    def test_handoff_states_exactly_match_v1_1(self) -> None:
        self.assertEqual(tuple(m.name for m in HandoffState), HANDOFF_STATE_NAMES)

    def test_handoff_state_count_has_no_extras(self) -> None:
        self.assertEqual(len(HandoffState), len(HANDOFF_STATE_NAMES))

    def test_values_equal_names(self) -> None:
        for member in HandoffState:
            with self.subTest(state=member.name):
                self.assertEqual(member.value, member.name)

    def test_serialises_as_protocol_string(self) -> None:
        self.assertEqual(str(HandoffState.IN_PROGRESS), "IN_PROGRESS")
        self.assertEqual(HandoffState("SUPERSEDED"), HandoffState.SUPERSEDED)

    def test_invented_handoff_state_rejected(self) -> None:
        with self.assertRaises(ValueError):
            HandoffState("INVENTED")


class ProjectArtifactTests(unittest.TestCase):
    """Project identity and version."""

    def test_identity_representable(self) -> None:
        project = ProjectArtifact(
            project_id="ai-router", project_version="v1.1-migration"
        )
        self.assertEqual(project.project_id, "ai-router")
        self.assertEqual(project.project_version, "v1.1-migration")

    def test_invalid_project_id_rejected(self) -> None:
        for bad in INVALID_IDENTITIES:
            with self.subTest(project_id=bad):
                with self.assertRaises(ArtifactValidationError):
                    ProjectArtifact(project_id=bad, project_version="v1")

    def test_invalid_project_version_rejected(self) -> None:
        for bad in INVALID_IDENTITIES:
            with self.subTest(project_version=bad):
                with self.assertRaises(ArtifactValidationError):
                    ProjectArtifact(project_id="ai-router", project_version=bad)

    def test_artifact_is_immutable(self) -> None:
        project = ProjectArtifact(project_id="ai-router", project_version="v1")
        with self.assertRaises(FrozenInstanceError):
            project.project_id = "other"  # type: ignore[misc]


class TaskArtifactTests(unittest.TestCase):
    """Task identity, version, requirement version, state, criteria,
    dependencies, and verification definition."""

    def _task(self, **overrides) -> TaskArtifact:
        base = dict(
            task_id="TASK-002",
            task_version="1",
            requirement_version="v1.1",
        )
        base.update(overrides)
        return TaskArtifact(**base)

    def test_defaults(self) -> None:
        task = self._task()
        self.assertEqual(task.state, TaskState.PENDING)
        self.assertEqual(task.acceptance_criteria, ())
        self.assertEqual(task.dependencies, ())
        self.assertIsNone(task.verification)

    def test_identity_fields_representable(self) -> None:
        task = self._task()
        self.assertEqual(task.task_id, "TASK-002")
        self.assertEqual(task.task_version, "1")
        self.assertEqual(task.requirement_version, "v1.1")

    def test_invalid_required_identity_rejected(self) -> None:
        for field in ("task_id", "task_version", "requirement_version"):
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ArtifactValidationError):
                        self._task(**{field: bad})

    def test_full_v1_1_capability_representation(self) -> None:
        verification = VerificationDefinition(
            method="pytest",
            description="Focused unit tests + full regression suite",
        )
        task = self._task(
            state=TaskState.VERIFYING,
            acceptance_criteria=(
                "Required identity fields can be represented",
                "Existing test suite remains passing",
            ),
            dependencies=("TASK-001",),
            verification=verification,
        )

        self.assertEqual(task.state, TaskState.VERIFYING)
        self.assertEqual(len(task.acceptance_criteria), 2)
        self.assertEqual(task.dependencies, ("TASK-001",))
        self.assertEqual(task.verification.method, "pytest")

    def test_every_v1_1_task_state_accepted(self) -> None:
        for name in TASK_STATE_NAMES:
            with self.subTest(state=name):
                task = self._task(state=name)
                self.assertIs(task.state, TaskState(name))

    def test_invented_task_state_rejected(self) -> None:
        for bad in ("INVENTED", "DONE", 7, None):
            with self.subTest(state=bad):
                with self.assertRaises(ArtifactValidationError):
                    self._task(state=bad)

    def test_handoff_only_state_rejected_for_task(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            self._task(state="DISPATCHED")

    def test_list_input_normalised_to_tuple(self) -> None:
        task = self._task(
            acceptance_criteria=["criterion one"],
            dependencies=["TASK-001"],
        )
        self.assertEqual(task.acceptance_criteria, ("criterion one",))
        self.assertEqual(task.dependencies, ("TASK-001",))

    def test_single_string_not_accepted_as_sequence(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            self._task(dependencies="TASK-001")
        with self.assertRaises(ArtifactValidationError):
            self._task(acceptance_criteria="single criterion")

    def test_blank_dependency_identity_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            self._task(dependencies=("TASK-001", ""))

    def test_invalid_verification_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            self._task(verification="pytest")

    def test_state_replace_creates_new_artifact(self) -> None:
        task = self._task()
        updated = replace(task, state=TaskState.IMPLEMENTING)
        self.assertEqual(task.state, TaskState.PENDING)
        self.assertEqual(updated.state, TaskState.IMPLEMENTING)
        self.assertEqual(updated.task_id, task.task_id)

    def test_artifact_is_immutable(self) -> None:
        task = self._task()
        with self.assertRaises(FrozenInstanceError):
            task.state = TaskState.COMPLETED  # type: ignore[misc]


class VerificationDefinitionTests(unittest.TestCase):
    """Verification definition representation and validation."""

    def test_method_and_description_representable(self) -> None:
        verification = VerificationDefinition(
            method="pytest",
            description="Focused tests, full suite, compileall",
        )
        self.assertEqual(verification.method, "pytest")
        self.assertEqual(
            verification.description, "Focused tests, full suite, compileall"
        )

    def test_description_defaults_empty(self) -> None:
        self.assertEqual(VerificationDefinition(method="pytest").description, "")

    def test_invalid_method_rejected(self) -> None:
        for bad in INVALID_IDENTITIES:
            with self.subTest(method=bad):
                with self.assertRaises(ArtifactValidationError):
                    VerificationDefinition(method=bad)

    def test_non_string_description_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            VerificationDefinition(method="pytest", description=None)  # type: ignore[arg-type]


class HandoffArtifactTests(unittest.TestCase):
    """Handoff identity and exact V1.1 lifecycle state."""

    def test_defaults_to_pending(self) -> None:
        handoff = HandoffArtifact(
            handoff_id="HANDOFF-TASK-002-001", correlation_id="CORR-TASK-002-001"
        )
        self.assertEqual(handoff.state, HandoffState.PENDING)

    def test_identity_and_correlation_representable(self) -> None:
        handoff = HandoffArtifact(
            handoff_id="HANDOFF-TASK-002-001",
            correlation_id="CORR-TASK-002-001",
            state=HandoffState.IN_PROGRESS,
        )
        self.assertEqual(handoff.handoff_id, "HANDOFF-TASK-002-001")
        self.assertEqual(handoff.correlation_id, "CORR-TASK-002-001")
        self.assertEqual(handoff.state, HandoffState.IN_PROGRESS)

    def test_invalid_required_identity_rejected(self) -> None:
        for field in ("handoff_id", "correlation_id"):
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    kwargs = {
                        "handoff_id": "HANDOFF-TASK-002-001",
                        "correlation_id": "CORR-TASK-002-001",
                    }
                    kwargs[field] = bad
                    with self.assertRaises(ArtifactValidationError):
                        HandoffArtifact(**kwargs)

    def test_every_v1_1_handoff_state_accepted(self) -> None:
        for name in HANDOFF_STATE_NAMES:
            with self.subTest(state=name):
                handoff = HandoffArtifact(
                    handoff_id="HANDOFF-TASK-002-001",
                    correlation_id="CORR-TASK-002-001",
                    state=name,
                )
                self.assertIs(handoff.state, HandoffState(name))

    def test_invented_handoff_state_rejected(self) -> None:
        for bad in ("INVENTED", "PENDING_REVIEW", 3, None):
            with self.subTest(state=bad):
                with self.assertRaises(ArtifactValidationError):
                    HandoffArtifact(
                        handoff_id="HANDOFF-TASK-002-001",
                        correlation_id="CORR-TASK-002-001",
                        state=bad,
                    )

    def test_task_only_state_rejected_for_handoff(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            HandoffArtifact(
                handoff_id="HANDOFF-TASK-002-001",
                correlation_id="CORR-TASK-002-001",
                state="IMPLEMENTING",
            )

    def test_artifact_is_immutable(self) -> None:
        handoff = HandoffArtifact(
            handoff_id="HANDOFF-TASK-002-001", correlation_id="CORR-TASK-002-001"
        )
        with self.assertRaises(FrozenInstanceError):
            handoff.state = HandoffState.PROCESSED  # type: ignore[misc]


class ExecutionArtifactTests(unittest.TestCase):
    """Execution identity, correlation ID, and idempotency key."""

    def test_all_fields_representable(self) -> None:
        execution = ExecutionArtifact(
            execution_id="EXEC-TASK-002-001",
            correlation_id="CORR-TASK-002-001",
            idempotency_key="TASK-002-v1-exec-001",
        )
        self.assertEqual(execution.execution_id, "EXEC-TASK-002-001")
        self.assertEqual(execution.correlation_id, "CORR-TASK-002-001")
        self.assertEqual(execution.idempotency_key, "TASK-002-v1-exec-001")

    def test_invalid_required_identity_rejected(self) -> None:
        fields = ("execution_id", "correlation_id", "idempotency_key")
        for field in fields:
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    kwargs = {
                        "execution_id": "EXEC-TASK-002-001",
                        "correlation_id": "CORR-TASK-002-001",
                        "idempotency_key": "TASK-002-v1-exec-001",
                    }
                    kwargs[field] = bad
                    with self.assertRaises(ArtifactValidationError):
                        ExecutionArtifact(**kwargs)

    def test_artifact_is_immutable(self) -> None:
        execution = ExecutionArtifact(
            execution_id="EXEC-TASK-002-001",
            correlation_id="CORR-TASK-002-001",
            idempotency_key="TASK-002-v1-exec-001",
        )
        with self.assertRaises(FrozenInstanceError):
            execution.execution_id = "other"  # type: ignore[misc]


class ExecutionRecordCompositionTests(unittest.TestCase):
    """The TASK-002 execution record values must be representable together."""

    def test_task_002_record_representable(self) -> None:
        project = ProjectArtifact(
            project_id="ai-router", project_version="v1.1-migration"
        )
        task = TaskArtifact(
            task_id="TASK-002",
            task_version="1",
            requirement_version="v1.1",
            state=TaskState.IMPLEMENTING,
            acceptance_criteria=(
                "Required V1.1 artifact identity fields can be represented",
                "Existing test suite remains passing",
            ),
            dependencies=("TASK-001",),
            verification=VerificationDefinition(
                method="Focused unit tests + full regression suite",
                description="pytest + compileall",
            ),
        )
        handoff = HandoffArtifact(
            handoff_id="HANDOFF-TASK-002-001",
            correlation_id="CORR-TASK-002-001",
            state=HandoffState.IN_PROGRESS,
        )
        execution = ExecutionArtifact(
            execution_id="EXEC-TASK-002-001",
            correlation_id="CORR-TASK-002-001",
            idempotency_key="TASK-002-v1-exec-001",
        )

        self.assertEqual(project.project_id, "ai-router")
        self.assertIs(task.state, TaskState.IMPLEMENTING)
        self.assertIs(handoff.state, HandoffState.IN_PROGRESS)
        # Handoff and execution share the correlation ID by construction.
        self.assertEqual(handoff.correlation_id, execution.correlation_id)


if __name__ == "__main__":
    unittest.main()
