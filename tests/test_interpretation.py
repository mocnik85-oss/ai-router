"""Tests for protocol.interpretation — Protocol V1.1 PM interpretation layer.

Covers the TASK-006 governance invariant:

    RAW EXECUTION STATE / RESULT
            -> PM INTERPRETATION
            -> AUTHORITATIVE TASK/PROJECT STATE

Offline only: no network, model, or provider calls.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import protocol.interpretation as interpretation_module
from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
)
from protocol.interpretation import PMDecision, PMDecisionKind, PMInterpreter
from protocol.results import EvidenceRecord, ExecutionResult, ResultStatus


# Exact decision definitions — order included.
DECISION_KIND_NAMES = (
    "COMPLETE",
    "REROUTE",
    "BLOCKED",
    "NEEDS_USER",
    "REJECTED",
)

#: Result statuses that never claim completion (SUCCESS is gated on
#: acceptance evidence and checked separately).
NON_COMPLETION_STATUSES = (
    "PARTIAL",
    "FAILURE",
    "BLOCKED",
    "NEEDS_USER",
)

INVALID_IDENTITIES = ("", "   ", None)

TASK_ID = "TASK-006"
TASK_VERSION = "1"
REQUIREMENT_VERSION = "v1.1"
EXECUTION_ID = "EXEC-TASK-006-001"
CORRELATION_ID = "CORR-TASK-006-001"
RESULT_ID = "RESULT-TASK-006-001"
PROJECT_ID = "ai-router"
PROJECT_VERSION = "v1.1-migration"

CRITERION_1 = (
    "Execution results are interpreted before authoritative "
    "task-state changes."
)
CRITERION_2 = (
    "A successful result cannot close a task without required "
    "acceptance evidence."
)
CRITERIA = (CRITERION_1, CRITERION_2)
METHOD = "Focused TASK-006 tests"

UNDEFINED_CRITERION = "A criterion the task never defined."


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _task(**overrides) -> TaskArtifact:
    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        state=TaskState.IMPLEMENTING,
        acceptance_criteria=CRITERIA,
        verification=VerificationDefinition(
            method=METHOD,
            description="focused tests + full regression + compileall",
        ),
    )
    base.update(overrides)
    return TaskArtifact(**base)


def _project() -> ProjectArtifact:
    return ProjectArtifact(
        project_id=PROJECT_ID, project_version=PROJECT_VERSION
    )


def _execution(**overrides) -> ExecutionArtifact:
    base = dict(
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        idempotency_key="TASK-006-v1-exec-001",
    )
    base.update(overrides)
    return ExecutionArtifact(**base)


def _evidence(
    evidence_id: str = "EVID-TASK-006-001",
    criterion: str = CRITERION_1,
    **overrides,
) -> EvidenceRecord:
    base = dict(
        evidence_id=evidence_id,
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        execution_id=EXECUTION_ID,
        requirement_version=REQUIREMENT_VERSION,
        acceptance_criterion=criterion,
        verification_method=METHOD,
        summary="focused test run passed",
    )
    base.update(overrides)
    return EvidenceRecord(**base)


def _full_evidence() -> tuple[EvidenceRecord, EvidenceRecord]:
    """One evidence record per stated acceptance criterion."""
    return (
        _evidence("EVID-TASK-006-001", CRITERION_1),
        _evidence("EVID-TASK-006-002", CRITERION_2),
    )


def _result(**overrides) -> ExecutionResult:
    base = dict(
        result_id=RESULT_ID,
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        status=ResultStatus.SUCCESS,
        acceptance_criteria=CRITERIA,
        evidence=_full_evidence(),
        summary="all acceptance criteria verified",
    )
    base.update(overrides)
    return ExecutionResult(**base)


def _success_claiming_one_criterion() -> ExecutionResult:
    """A SUCCESS result stating and evidencing only one of the two
    acceptance criteria the authoritative task declares."""

    return _result(
        acceptance_criteria=(CRITERION_1,),
        evidence=(_evidence("EVID-TASK-006-001", CRITERION_1),),
    )


def _interpret(result, task, execution, project=None) -> PMDecision:
    return PMInterpreter().interpret(
        result, task, execution, project=project
    )


def _decision(**overrides) -> PMDecision:
    """A directly constructed, internally consistent COMPLETE decision."""

    base = dict(
        decision_id=f"DECISION-{RESULT_ID}",
        kind=PMDecisionKind.COMPLETE,
        project=_project(),
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        observed_task_state=TaskState.IMPLEMENTING,
        requirement_version=REQUIREMENT_VERSION,
        result_id=RESULT_ID,
        result_status=ResultStatus.SUCCESS,
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        acceptance_criteria=CRITERIA,
        covered_criteria=CRITERIA,
        evidence=_full_evidence(),
        reasons=(
            "SUCCESS with acceptance evidence covering every "
            "acceptance criterion.",
        ),
    )
    base.update(overrides)
    return PMDecision(**base)


def _source_tree() -> ast.Module:
    source = Path(
        inspect.getsourcefile(interpretation_module) or ""
    ).read_text(encoding="utf-8")
    return ast.parse(source)


# ------------------------------------------------------------------
# Decision kinds
# ------------------------------------------------------------------

class PMDecisionKindTests(unittest.TestCase):
    """PM decisions are explicit, closed, and are not task states."""

    def test_decision_kinds_defined_exactly(self) -> None:
        self.assertEqual(
            tuple(m.name for m in PMDecisionKind), DECISION_KIND_NAMES
        )

    def test_decision_kind_count_has_no_extras(self) -> None:
        self.assertEqual(len(PMDecisionKind), len(DECISION_KIND_NAMES))

    def test_values_equal_names(self) -> None:
        for member in PMDecisionKind:
            with self.subTest(kind=member.name):
                self.assertEqual(member.value, member.name)

    def test_serialises_as_protocol_string(self) -> None:
        self.assertEqual(str(PMDecisionKind.COMPLETE), "COMPLETE")
        self.assertEqual(
            PMDecisionKind("REROUTE"), PMDecisionKind.REROUTE
        )

    def test_invented_decision_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PMDecisionKind("INVENTED")

    def test_lifecycle_states_are_not_decision_kinds(self) -> None:
        # V1.1 task/handoff lifecycle states may never be produced or
        # consumed as PM decisions by this layer.
        for foreign in (
            "COMPLETED",
            "PENDING",
            "READY",
            "VERIFYING",
            "DISPATCHED",
            "IN_PROGRESS",
            "PROCESSED",
        ):
            with self.subTest(foreign=foreign):
                with self.assertRaises(ValueError):
                    PMDecisionKind(foreign)

    def test_decision_kinds_are_not_task_state_members(self) -> None:
        self.assertIsNot(PMDecisionKind.BLOCKED, TaskState.BLOCKED)
        self.assertIsNot(PMDecisionKind.NEEDS_USER, TaskState.NEEDS_USER)
        for member in PMDecisionKind:
            with self.subTest(kind=member.name):
                self.assertFalse(isinstance(member, TaskState))


# ------------------------------------------------------------------
# Result status -> PM decision
# ------------------------------------------------------------------

class InterpretationDecisionTests(unittest.TestCase):
    """Each ResultStatus is interpreted into an explicit PM decision."""

    def test_success_with_complete_evidence_is_completion(self) -> None:
        decision = _interpret(_result(), _task(), _execution())
        self.assertIs(decision.kind, PMDecisionKind.COMPLETE)
        self.assertIs(decision.result_status, ResultStatus.SUCCESS)
        self.assertEqual(decision.acceptance_criteria, CRITERIA)
        self.assertEqual(decision.covered_criteria, CRITERIA)
        self.assertEqual(len(decision.evidence), 2)
        self.assertTrue(decision.reasons)
        self.assertIs(
            decision.observed_task_state, TaskState.IMPLEMENTING
        )

    def test_success_without_required_evidence_is_not_completion(
        self,
    ) -> None:
        # ExecutionResult allows a SUCCESS claim whose stated criteria
        # are covered; the interpretation layer must still check them
        # against every acceptance criterion the task declares.
        decision = _interpret(
            _success_claiming_one_criterion(), _task(), _execution()
        )
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)
        self.assertIs(decision.kind, PMDecisionKind.REJECTED)
        self.assertIs(decision.result_status, ResultStatus.SUCCESS)
        self.assertEqual(decision.covered_criteria, (CRITERION_1,))
        self.assertEqual(decision.acceptance_criteria, CRITERIA)
        self.assertTrue(
            any(CRITERION_2 in reason for reason in decision.reasons)
        )

    def test_partial_is_distinguishable_non_completion(self) -> None:
        decision = _interpret(
            _result(status="PARTIAL", evidence=()), _task(), _execution()
        )
        self.assertIs(decision.kind, PMDecisionKind.REROUTE)
        self.assertIs(decision.result_status, ResultStatus.PARTIAL)
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)
        self.assertTrue(
            any("not complete" in reason for reason in decision.reasons)
        )

    def test_partial_with_full_evidence_is_still_not_completion(
        self,
    ) -> None:
        decision = _interpret(_result(status="PARTIAL"), _task(), _execution())
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)
        self.assertIs(decision.result_status, ResultStatus.PARTIAL)

    def test_failure_is_failure_reroute_handling(self) -> None:
        decision = _interpret(
            _result(
                status="FAILURE",
                evidence=(),
                summary="execution failed",
                detail="No suitable free model found.",
            ),
            _task(),
            _execution(),
        )
        self.assertIs(decision.kind, PMDecisionKind.REROUTE)
        self.assertIs(decision.result_status, ResultStatus.FAILURE)
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)
        self.assertTrue(decision.reasons)

    def test_blocked_is_blocked_decision(self) -> None:
        decision = _interpret(
            _result(status="BLOCKED", evidence=()), _task(), _execution()
        )
        self.assertIs(decision.kind, PMDecisionKind.BLOCKED)
        self.assertIs(decision.result_status, ResultStatus.BLOCKED)
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)

    def test_needs_user_is_user_required_decision(self) -> None:
        decision = _interpret(
            _result(status="NEEDS_USER", evidence=()), _task(), _execution()
        )
        self.assertIs(decision.kind, PMDecisionKind.NEEDS_USER)
        self.assertIs(decision.result_status, ResultStatus.NEEDS_USER)
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)

    def test_failure_partial_blocked_needs_user_distinguishable(
        self,
    ) -> None:
        observed: dict[str, tuple[object, object]] = {}
        for status in NON_COMPLETION_STATUSES:
            decision = _interpret(
                _result(status=status, evidence=()), _task(), _execution()
            )
            observed[status] = (decision.kind, decision.result_status)

        # Pairwise distinguishable decisions, never COMPLETE.
        self.assertEqual(len(set(observed.values())), len(observed))
        self.assertIs(observed["PARTIAL"][0], PMDecisionKind.REROUTE)
        self.assertIs(observed["FAILURE"][0], PMDecisionKind.REROUTE)
        self.assertIs(observed["BLOCKED"][0], PMDecisionKind.BLOCKED)
        self.assertIs(observed["NEEDS_USER"][0], PMDecisionKind.NEEDS_USER)
        for status, (kind, result_status) in observed.items():
            with self.subTest(status=status):
                self.assertIsNot(kind, PMDecisionKind.COMPLETE)
                self.assertIs(result_status, ResultStatus(status))

    def test_interpretation_is_deterministic(self) -> None:
        first = _interpret(_result(), _task(), _execution())
        second = _interpret(_result(), _task(), _execution())
        self.assertEqual(first, second)
        self.assertEqual(first.decision_id, second.decision_id)


# ------------------------------------------------------------------
# Rejected / safely routed combinations
# ------------------------------------------------------------------

class RejectedCombinationTests(unittest.TestCase):
    """Contradictory or invalid result/evidence combinations are
    rejected as explicit decisions, never raised away or copied."""

    def _assert_rejected(self, result, *, expected: str) -> PMDecision:
        decision = _interpret(result, _task(), _execution())
        self.assertIs(decision.kind, PMDecisionKind.REJECTED)
        self.assertIsNot(decision.kind, PMDecisionKind.COMPLETE)
        self.assertTrue(
            any(expected in reason for reason in decision.reasons),
            f"expected {expected!r} in {decision.reasons!r}",
        )
        return decision

    def test_task_identity_mismatch_rejected(self) -> None:
        decision = self._assert_rejected(
            _result(status="FAILURE", evidence=(), task_id="TASK-999"),
            expected="bound to task",
        )
        # The decision stays anchored to the authoritative task; the
        # contradicting claim is preserved in the reasons.
        self.assertEqual(decision.task_id, TASK_ID)
        self.assertTrue(
            any("TASK-999" in reason for reason in decision.reasons)
        )

    def test_task_version_mismatch_rejected(self) -> None:
        decision = self._assert_rejected(
            _result(status="FAILURE", evidence=(), task_version="2"),
            expected="bound to task",
        )
        self.assertEqual(decision.task_version, TASK_VERSION)

    def test_execution_identity_mismatch_rejected(self) -> None:
        decision = self._assert_rejected(
            _result(
                status="FAILURE", evidence=(),
                execution_id="EXEC-TASK-006-999",
            ),
            expected="EXEC-TASK-006-999",
        )
        self.assertEqual(decision.execution_id, EXECUTION_ID)

    def test_correlation_identity_mismatch_rejected(self) -> None:
        decision = self._assert_rejected(
            _result(status="FAILURE", evidence=(), correlation_id="CORR-OTHER"),
            expected="correlation",
        )
        self.assertEqual(decision.correlation_id, CORRELATION_ID)
        self.assertTrue(
            any("CORR-OTHER" in reason for reason in decision.reasons)
        )

    def test_requirement_version_mismatch_rejected(self) -> None:
        decision = self._assert_rejected(
            _result(status="FAILURE", evidence=(), requirement_version="v2"),
            expected="requirement version",
        )
        self.assertEqual(decision.requirement_version, REQUIREMENT_VERSION)
        self.assertTrue(
            any("'v2'" in reason for reason in decision.reasons)
        )
        self.assertEqual(
            decision.observed_task_state, TaskState.IMPLEMENTING
        )

    def test_evidence_citing_undefined_criterion_rejected(self) -> None:
        # ExecutionResult accepts evidence for criteria the result
        # states; only the interpretation layer knows the authoritative
        # task's criteria and rejects the contradiction.
        result = _result(
            acceptance_criteria=(CRITERION_1, UNDEFINED_CRITERION),
            evidence=(
                _evidence("EVID-TASK-006-001", CRITERION_1),
                _evidence("EVID-TASK-006-003", UNDEFINED_CRITERION),
            ),
        )
        decision = self._assert_rejected(result, expected="not defined by")
        # Evidence is retained so the PM can inspect what was rejected.
        self.assertEqual(decision.evidence, result.evidence)

    def test_evidence_verification_method_mismatch_rejected(self) -> None:
        result = _result(
            evidence=(
                _evidence(
                    "EVID-TASK-006-001",
                    CRITERION_1,
                    verification_method="manual inspection",
                ),
                _evidence("EVID-TASK-006-002", CRITERION_2),
            )
        )
        self._assert_rejected(result, expected="verification method")

    def test_wrong_argument_types_raise(self) -> None:
        cases = (
            (
                "result must be an ExecutionResult",
                ("not a result", _task(), _execution(), None),
            ),
            (
                "task must be a TaskArtifact",
                (_result(), "not a task", _execution(), None),
            ),
            (
                "execution must be an ExecutionArtifact",
                (_result(), _task(), "not an execution", None),
            ),
            (
                "project must be a ProjectArtifact or None",
                (_result(), _task(), _execution(), "not a project"),
            ),
        )
        for message, (result, task, execution, project) in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ArtifactValidationError, message):
                    _interpret(result, task, execution, project)


# ------------------------------------------------------------------
# Authoritative state isolation
# ------------------------------------------------------------------

class AuthoritativeStateIsolationTests(unittest.TestCase):
    """Interpretation produces decisions; it never mutates or forges
    authoritative project/task state."""

    def test_interpretation_never_mutates_authoritative_task_state(
        self,
    ) -> None:
        cases = (
            _result(),                                   # -> COMPLETE
            _success_claiming_one_criterion(),           # -> REJECTED
            _result(status="FAILURE", evidence=()),      # -> REROUTE
            _result(status="BLOCKED", evidence=()),      # -> BLOCKED
            _result(status="NEEDS_USER", evidence=()),   # -> NEEDS_USER
        )
        for result in cases:
            task = _task()
            original = _task()
            decision = _interpret(result, task, _execution())
            with self.subTest(kind=decision.kind, status=result.status):
                self.assertEqual(task, original)
                self.assertIs(task.state, TaskState.IMPLEMENTING)
                self.assertIsNot(task, decision)
                self.assertNotIsInstance(decision, TaskArtifact)
                # The only task state on the decision is a read-only
                # snapshot of the unchanged authoritative state.
                state_fields = [
                    field.name
                    for field in fields(decision)
                    if isinstance(getattr(decision, field.name), TaskState)
                ]
                self.assertEqual(state_fields, ["observed_task_state"])
                self.assertIs(decision.observed_task_state, task.state)

    def test_complete_decision_leaves_task_open(self) -> None:
        # Even the strongest decision must not close the task: an
        # explicit completion decision is produced, the task state is
        # untouched, and the PM remains the transition authority.
        task = _task(state=TaskState.IMPLEMENTING)
        decision = _interpret(_result(), task, _execution())
        self.assertIs(decision.kind, PMDecisionKind.COMPLETE)
        self.assertIs(task.state, TaskState.IMPLEMENTING)
        self.assertIsNot(task.state, TaskState.COMPLETED)

    def test_decision_is_immutable(self) -> None:
        decision = _interpret(_result(), _task(), _execution())
        with self.assertRaises(FrozenInstanceError):
            decision.kind = PMDecisionKind.REJECTED  # type: ignore[misc]

    def test_module_forges_no_authoritative_artifact(self) -> None:
        tree = _source_tree()
        forged = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            in {
                "ProjectArtifact",
                "TaskArtifact",
                "HandoffArtifact",
                "ExecutionArtifact",
                "VerificationDefinition",
            }
        ]
        self.assertEqual(forged, [])

    def test_module_never_assigns_to_input_artifacts(self) -> None:
        tree = _source_tree()
        stores = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id
            in {"task", "result", "execution", "project", "decision"}
        ]
        self.assertEqual(stores, [])
        setattr_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setattr"
        ]
        self.assertEqual(setattr_calls, [])


# ------------------------------------------------------------------
# Decision traceability
# ------------------------------------------------------------------

class DecisionTraceabilityTests(unittest.TestCase):
    """Decisions retain traceability to result, evidence, and the
    full V1.1 identity set."""

    def test_decision_retains_full_traceability(self) -> None:
        project = _project()
        result = _result()
        decision = _interpret(result, _task(), _execution(), project=project)

        self.assertEqual(decision.decision_id, f"DECISION-{RESULT_ID}")
        self.assertIs(decision.project, project)
        self.assertEqual(decision.project.project_id, PROJECT_ID)
        self.assertEqual(decision.project.project_version, PROJECT_VERSION)
        self.assertEqual(decision.task_id, TASK_ID)
        self.assertEqual(decision.task_version, TASK_VERSION)
        self.assertEqual(decision.requirement_version, REQUIREMENT_VERSION)
        self.assertEqual(decision.result_id, RESULT_ID)
        self.assertEqual(decision.execution_id, EXECUTION_ID)
        self.assertEqual(decision.correlation_id, CORRELATION_ID)
        self.assertEqual(decision.acceptance_criteria, CRITERIA)
        self.assertEqual(decision.covered_criteria, CRITERIA)
        self.assertEqual(decision.evidence, result.evidence)
        self.assertTrue(decision.reasons)

    def test_decision_without_project_context_is_allowed(self) -> None:
        decision = _interpret(_result(), _task(), _execution())
        self.assertIsNone(decision.project)

    def test_rejected_decision_still_retains_traceability(self) -> None:
        result = _result(
            acceptance_criteria=(CRITERION_1, UNDEFINED_CRITERION),
            evidence=(
                _evidence("EVID-TASK-006-001", CRITERION_1),
                _evidence("EVID-TASK-006-003", UNDEFINED_CRITERION),
            ),
        )
        decision = _interpret(result, _task(), _execution())
        self.assertIs(decision.kind, PMDecisionKind.REJECTED)
        self.assertEqual(decision.result_id, RESULT_ID)
        self.assertEqual(decision.task_id, TASK_ID)
        self.assertEqual(decision.execution_id, EXECUTION_ID)
        self.assertEqual(decision.correlation_id, CORRELATION_ID)
        self.assertEqual(decision.requirement_version, REQUIREMENT_VERSION)
        self.assertEqual(decision.evidence, result.evidence)

    def test_decision_kind_and_status_accepted_as_plain_strings(
        self,
    ) -> None:
        decision = _decision(
            kind="REJECTED", result_status=ResultStatus.SUCCESS
        )
        self.assertIs(decision.kind, PMDecisionKind.REJECTED)
        self.assertIs(decision.result_status, ResultStatus.SUCCESS)


# ------------------------------------------------------------------
# Decision record validation
# ------------------------------------------------------------------

class PMDecisionRecordTests(unittest.TestCase):
    """The decision artifact validates contradictory records itself."""

    def test_invalid_required_identity_rejected(self) -> None:
        fields_to_check = (
            "decision_id",
            "task_id",
            "task_version",
            "requirement_version",
            "result_id",
            "execution_id",
            "correlation_id",
        )
        for field in fields_to_check:
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ArtifactValidationError):
                        _decision(**{field: bad})

    def test_invented_decision_kind_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(kind="INVENTED")

    def test_task_lifecycle_state_rejected_as_kind(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(kind="COMPLETED")

    def test_invented_result_status_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(result_status="DONE")

    def test_non_project_context_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(project="ai-router")

    def test_complete_requires_success_result_status(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "SUCCESS result status"
        ):
            _decision(
                kind=PMDecisionKind.COMPLETE,
                result_status=ResultStatus.PARTIAL,
            )

    def test_success_result_status_cannot_pair_with_other_kind(
        self,
    ) -> None:
        for kind in (
            PMDecisionKind.REROUTE,
            PMDecisionKind.BLOCKED,
            PMDecisionKind.NEEDS_USER,
        ):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(
                    ArtifactValidationError, "cannot be recorded"
                ):
                    _decision(kind=kind, result_status=ResultStatus.SUCCESS)

    def test_complete_requires_acceptance_criteria(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "requires acceptance criteria"
        ):
            _decision(
                acceptance_criteria=(),
                covered_criteria=(),
                evidence=(),
            )

    def test_complete_requires_coverage_of_every_criterion(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError,
            "covering every acceptance criterion",
        ):
            _decision(
                acceptance_criteria=CRITERIA,
                covered_criteria=(CRITERION_1,),
                evidence=(_evidence("EVID-TASK-006-001", CRITERION_1),),
            )

    def test_covered_criteria_must_be_backed_by_evidence(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "does not match the cited evidence"
        ):
            _decision(
                kind=PMDecisionKind.REJECTED,
                acceptance_criteria=CRITERIA,
                covered_criteria=(CRITERION_2,),
                evidence=(_evidence("EVID-TASK-006-001", CRITERION_1),),
            )

    def test_covered_criteria_outside_acceptance_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "outside acceptance_criteria"
        ):
            _decision(
                kind=PMDecisionKind.REJECTED,
                acceptance_criteria=CRITERIA,
                covered_criteria=("Not a declared criterion.",),
                evidence=(),
            )

    def test_single_value_reasons_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(reasons="one reason")  # type: ignore[arg-type]

    def test_non_evidence_entries_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _decision(evidence=("EVID-TASK-006-001",))  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Architectural independence
# ------------------------------------------------------------------

class InterpretationIndependenceTests(unittest.TestCase):
    """Interpretation must stay independent of JEV/providers so it is
    testable without execution/provider code."""

    def test_interpretation_module_imports_governance_only(self) -> None:
        tree = _source_tree()

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")

        roots = {name.split(".")[0] for name in imported}
        allowed = {
            "__future__",
            "dataclasses",
            "enum",
            "protocol",
        }
        self.assertTrue(
            roots <= allowed,
            f"protocol.interpretation imports outside governance scope: "
            f"{sorted(roots - allowed)}",
        )
        # It must reuse the TASK-002/TASK-005 models, not duplicate them.
        self.assertIn("protocol.artifacts", imported)
        self.assertIn("protocol.results", imported)
        self.assertNotIn("router", roots)
        self.assertNotIn("providers", roots)


if __name__ == "__main__":
    unittest.main()
