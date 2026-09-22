"""Tests for protocol.results — Protocol V1.1 structured result and evidence model.

Offline only: no network, model, or provider calls.
"""

from __future__ import annotations

import ast
import inspect
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

import protocol.results as results_module
from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    TaskArtifact,
    VerificationDefinition,
)
from protocol.results import (
    EvidenceRecord,
    ExecutionResult,
    ResultStatus,
    validate_traceability,
)
from router.jev import JEVResult


# Exact V1.1 result statuses — order included.
RESULT_STATUS_NAMES = (
    "SUCCESS",
    "PARTIAL",
    "FAILURE",
    "BLOCKED",
    "NEEDS_USER",
)

#: Statuses that do not claim completion (SUCCESS is checked separately).
NON_COMPLETION_STATUSES = (
    "PARTIAL",
    "FAILURE",
    "BLOCKED",
    "NEEDS_USER",
)

INVALID_IDENTITIES = ("", "   ", None)

TASK_ID = "TASK-005"
TASK_VERSION = "1"
REQUIREMENT_VERSION = "v1.1"
EXECUTION_ID = "EXEC-TASK-005-001"
CORRELATION_ID = "CORR-TASK-005-001"
RESULT_ID = "RESULT-TASK-005-001"

CRITERION_1 = "A structured V1.1 result model exists."
CRITERION_2 = "Evidence can be traced to acceptance requirements."
CRITERIA = (CRITERION_1, CRITERION_2)
METHOD = "Focused TASK-005 tests"


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _task(**overrides) -> TaskArtifact:
    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        acceptance_criteria=CRITERIA,
        verification=VerificationDefinition(
            method=METHOD,
            description="focused tests + full regression + compileall",
        ),
    )
    base.update(overrides)
    return TaskArtifact(**base)


def _execution(**overrides) -> ExecutionArtifact:
    base = dict(
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        idempotency_key="TASK-005-v1-exec-001",
    )
    base.update(overrides)
    return ExecutionArtifact(**base)


def _evidence(
    evidence_id: str = "EVID-TASK-005-001",
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
        _evidence("EVID-TASK-005-001", CRITERION_1),
        _evidence("EVID-TASK-005-002", CRITERION_2),
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


# ------------------------------------------------------------------
# Result status
# ------------------------------------------------------------------

class ResultStatusTests(unittest.TestCase):
    """Result status must be represented explicitly and exactly."""

    def test_result_statuses_defined_exactly(self) -> None:
        self.assertEqual(
            tuple(m.name for m in ResultStatus), RESULT_STATUS_NAMES
        )

    def test_status_count_has_no_extras(self) -> None:
        self.assertEqual(len(ResultStatus), len(RESULT_STATUS_NAMES))

    def test_values_equal_names(self) -> None:
        for member in ResultStatus:
            with self.subTest(status=member.name):
                self.assertEqual(member.value, member.name)

    def test_serialises_as_protocol_string(self) -> None:
        self.assertEqual(str(ResultStatus.SUCCESS), "SUCCESS")
        self.assertEqual(ResultStatus("NEEDS_USER"), ResultStatus.NEEDS_USER)

    def test_invented_status_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ResultStatus("INVENTED")

    def test_status_accepted_as_plain_string(self) -> None:
        for name in RESULT_STATUS_NAMES:
            with self.subTest(status=name):
                result = _result(status=name)
                self.assertIs(result.status, ResultStatus(name))

    def test_status_accepted_as_enum_member(self) -> None:
        result = _result(status=ResultStatus.PARTIAL)
        self.assertIs(result.status, ResultStatus.PARTIAL)

    def test_invalid_status_rejected_by_result(self) -> None:
        for bad in ("DONE", "", None, 7):
            with self.subTest(status=bad):
                with self.assertRaises(ArtifactValidationError):
                    _result(status=bad)

    def test_foreign_lifecycle_state_rejected_as_status(self) -> None:
        # Task lifecycle and handoff lifecycle states are not result statuses.
        for foreign in ("COMPLETED", "DISPATCHED"):
            with self.subTest(status=foreign):
                with self.assertRaises(ArtifactValidationError):
                    _result(status=foreign)


# ------------------------------------------------------------------
# Evidence records
# ------------------------------------------------------------------

class EvidenceRecordTests(unittest.TestCase):
    """Evidence must carry the full chain binding."""

    def test_all_fields_representable(self) -> None:
        record = _evidence(reference="tests/test_results.py::ResultStatusTests")
        self.assertEqual(record.evidence_id, "EVID-TASK-005-001")
        self.assertEqual(record.task_id, TASK_ID)
        self.assertEqual(record.task_version, TASK_VERSION)
        self.assertEqual(record.execution_id, EXECUTION_ID)
        self.assertEqual(record.requirement_version, REQUIREMENT_VERSION)
        self.assertEqual(record.acceptance_criterion, CRITERION_1)
        self.assertEqual(record.verification_method, METHOD)
        self.assertEqual(record.summary, "focused test run passed")

    def test_reference_defaults_to_empty(self) -> None:
        self.assertEqual(_evidence().reference, "")

    def test_invalid_required_identity_rejected(self) -> None:
        fields = (
            "evidence_id",
            "task_id",
            "task_version",
            "execution_id",
            "requirement_version",
            "acceptance_criterion",
            "verification_method",
            "summary",
        )
        for field in fields:
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ArtifactValidationError):
                        _evidence(**{field: bad})

    def test_non_string_reference_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _evidence(reference=None)  # type: ignore[arg-type]

    def test_artifact_is_immutable(self) -> None:
        record = _evidence()
        with self.assertRaises(FrozenInstanceError):
            record.summary = "edited"  # type: ignore[misc]


# ------------------------------------------------------------------
# Structured result
# ------------------------------------------------------------------

class ExecutionResultTests(unittest.TestCase):
    """Result identity/version fields, status, and evidence binding."""

    def test_identity_and_version_fields_representable(self) -> None:
        result = _result()
        self.assertEqual(result.result_id, RESULT_ID)
        self.assertEqual(result.task_id, TASK_ID)
        self.assertEqual(result.task_version, TASK_VERSION)
        self.assertEqual(result.requirement_version, REQUIREMENT_VERSION)
        self.assertEqual(result.execution_id, EXECUTION_ID)
        self.assertEqual(result.correlation_id, CORRELATION_ID)

    def test_invalid_required_identity_rejected(self) -> None:
        fields = (
            "result_id",
            "task_id",
            "task_version",
            "requirement_version",
            "execution_id",
            "correlation_id",
        )
        for field in fields:
            for bad in INVALID_IDENTITIES:
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ArtifactValidationError):
                        _result(**{field: bad})

    def test_single_value_evidence_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _result(evidence="EVID-TASK-005-001")  # type: ignore[arg-type]
        with self.assertRaises(ArtifactValidationError):
            _result(evidence=_evidence())  # type: ignore[arg-type]

    def test_non_evidence_entries_rejected(self) -> None:
        for bad_entry in ("EVID-TASK-005-001", {"evidence_id": "EVID-1"}, None):
            with self.subTest(entry=bad_entry):
                with self.assertRaises(ArtifactValidationError):
                    _result(evidence=(bad_entry,))  # type: ignore[arg-type]

    def test_list_input_normalised_to_tuple(self) -> None:
        result = _result(evidence=list(_full_evidence()))
        self.assertIsInstance(result.evidence, tuple)
        self.assertEqual(len(result.evidence), 2)

    def test_single_string_acceptance_criteria_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            _result(
                status=ResultStatus.FAILURE,
                evidence=(),
                acceptance_criteria="single criterion",  # type: ignore[arg-type]
            )

    def test_duplicate_evidence_id_rejected(self) -> None:
        duplicate = _evidence("EVID-TASK-005-001", CRITERION_2)
        with self.assertRaises(ArtifactValidationError):
            _result(evidence=(_evidence(), duplicate))

    def test_evidence_for_other_task_or_execution_rejected(self) -> None:
        foreign_values = (
            ("task_id", "TASK-999"),
            ("task_version", "2"),
            ("execution_id", "EXEC-OTHER"),
            ("requirement_version", "v2"),
        )
        for field, value in foreign_values:
            with self.subTest(field=field, value=value):
                foreign = _evidence(**{field: value})
                with self.assertRaises(ArtifactValidationError):
                    _result(evidence=(foreign, _evidence("EVID-2", CRITERION_2)))

    def test_evidence_citing_unstated_criterion_rejected(self) -> None:
        stray = _evidence("EVID-TASK-005-003", "A criterion this result omits.")
        with self.assertRaises(ArtifactValidationError):
            _result(acceptance_criteria=(CRITERION_1,), evidence=(_evidence(), stray))

    def test_non_string_summary_or_detail_rejected(self) -> None:
        for field in ("summary", "detail"):
            for bad in (None, 7):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ArtifactValidationError):
                        _result(**{field: bad})

    def test_optional_payload_accepted(self) -> None:
        payload = {"exit_code": 0, "text": "146 passed"}
        result = _result(payload=payload)
        self.assertEqual(result.payload, payload)

    def test_evidence_for_returns_only_matching_records(self) -> None:
        result = _result()
        first = result.evidence_for(CRITERION_1)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].evidence_id, "EVID-TASK-005-001")
        self.assertEqual(result.evidence_for("Not stated."), ())

    def test_covered_criteria_follows_stated_order(self) -> None:
        result = _result()
        self.assertEqual(result.covered_criteria, CRITERIA)

    def test_artifact_is_immutable(self) -> None:
        result = _result()
        with self.assertRaises(FrozenInstanceError):
            result.status = ResultStatus.FAILURE  # type: ignore[misc]


# ------------------------------------------------------------------
# Completion gate
# ------------------------------------------------------------------

class CompletionEvidenceGateTests(unittest.TestCase):
    """Results cannot claim completion without required evidence."""

    def test_success_without_evidence_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "without required evidence"
        ):
            _result(evidence=())

    def test_success_without_acceptance_criteria_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "without stated acceptance criteria"
        ):
            _result(acceptance_criteria=(), evidence=())

    def test_success_with_partial_evidence_coverage_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError,
            "without required evidence for acceptance criteria",
        ):
            _result(evidence=(_evidence(),))

    def test_success_with_full_evidence_coverage_accepted(self) -> None:
        result = _result()
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertTrue(result.claims_completion)
        self.assertEqual(result.covered_criteria, CRITERIA)

    def test_non_completion_statuses_representable_without_evidence(
        self,
    ) -> None:
        for name in NON_COMPLETION_STATUSES:
            with self.subTest(status=name):
                result = _result(status=name, evidence=())
                self.assertIs(result.status, ResultStatus(name))
                self.assertFalse(result.claims_completion)
                self.assertEqual(result.covered_criteria, ())

    def test_failure_result_preserves_detail(self) -> None:
        result = _result(
            status="FAILURE",
            evidence=(),
            summary="execution failed",
            detail="No suitable free model found.",
        )
        self.assertIs(result.status, ResultStatus.FAILURE)
        self.assertEqual(result.detail, "No suitable free model found.")

    def test_result_with_evidence_still_valid_for_non_success(self) -> None:
        result = _result(status="PARTIAL")
        self.assertIs(result.status, ResultStatus.PARTIAL)
        self.assertFalse(result.claims_completion)
        self.assertEqual(len(result.evidence), 2)


# ------------------------------------------------------------------
# Traceability chain
# ------------------------------------------------------------------

class TraceabilityTests(unittest.TestCase):
    """Requirement -> Acceptance Criterion -> Verification Method
    -> Evidence -> Result must validate against the task and execution."""

    def test_consistent_chain_validates(self) -> None:
        validate_traceability(_result(), _task(), _execution())

    def test_result_task_identity_mismatch_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            validate_traceability(
                _result(status="FAILURE", evidence=(), task_id="TASK-999"),
                _task(),
                _execution(),
            )
        with self.assertRaises(ArtifactValidationError):
            validate_traceability(
                _result(status="FAILURE", evidence=(), task_version="2"),
                _task(),
                _execution(),
            )

    def test_requirement_version_mismatch_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            validate_traceability(
                _result(status="FAILURE", evidence=(), requirement_version="v2"),
                _task(),
                _execution(),
            )

    def test_execution_identity_mismatch_rejected(self) -> None:
        with self.assertRaises(ArtifactValidationError):
            validate_traceability(
                _result(status="FAILURE", evidence=(), execution_id="EXEC-OTHER"),
                _task(),
                _execution(),
            )
        with self.assertRaises(ArtifactValidationError):
            validate_traceability(
                _result(status="FAILURE", evidence=(), correlation_id="CORR-OTHER"),
                _task(),
                _execution(),
            )

    def test_criterion_not_defined_by_task_rejected(self) -> None:
        with self.assertRaisesRegex(
            ArtifactValidationError, "not defined by"
        ):
            validate_traceability(
                _result(
                    status="FAILURE",
                    evidence=(),
                    acceptance_criteria=("A criterion the task never defined.",),
                ),
                _task(),
                _execution(),
            )

    def test_evidence_verification_method_mismatch_rejected(self) -> None:
        evidence = (
            _evidence("EVID-TASK-005-001", CRITERION_1,
                      verification_method="manual inspection"),
            _evidence("EVID-TASK-005-002", CRITERION_2),
        )
        with self.assertRaisesRegex(
            ArtifactValidationError, "verification method"
        ):
            validate_traceability(
                _result(evidence=evidence), _task(), _execution()
            )

    def test_method_check_skipped_when_task_declares_no_verification(
        self,
    ) -> None:
        validate_traceability(_result(), _task(verification=None), _execution())

    def test_wrong_argument_types_rejected(self) -> None:
        cases = (
            ("result must be an ExecutionResult",
             ("not a result", _task(), _execution())),
            ("task must be a TaskArtifact",
             (_result(), "not a task", _execution())),
            ("execution must be an ExecutionArtifact",
             (_result(), _task(), "not an execution")),
        )
        for message, args in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ArtifactValidationError, message):
                    validate_traceability(*args)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# JEV compatibility
# ------------------------------------------------------------------

class JEVResultCompatibilityTests(unittest.TestCase):
    """Existing JEV results remain representable in the new model."""

    def test_jev_failure_result_representable(self) -> None:
        jev = JEVResult(
            success=False,
            prompt="route this",
            model="",
            exit_code=-1,
            text="",
            error="No suitable free model found.",
        )
        result = _result(
            status=ResultStatus.SUCCESS if jev.success else ResultStatus.FAILURE,
            acceptance_criteria=(),
            evidence=(),
            summary="JEV orchestration failed" if not jev.success else "",
            detail=jev.error or "",
            payload=jev,
        )
        self.assertIs(result.status, ResultStatus.FAILURE)
        self.assertFalse(result.claims_completion)
        self.assertEqual(result.detail, "No suitable free model found.")
        self.assertIs(result.payload, jev)

    def test_jev_success_result_representable_with_evidence(self) -> None:
        jev = JEVResult(
            success=True,
            prompt="route this",
            model="free/model",
            exit_code=0,
            text="146 passed",
            events=[{"type": "stdout", "raw": "146 passed"}],
        )
        result = _result(detail=jev.text, payload=jev)
        self.assertIs(result.status, ResultStatus.SUCCESS)
        self.assertTrue(result.claims_completion)
        self.assertEqual(result.payload, jev)
        self.assertEqual(result.detail, "146 passed")

    def test_payload_carries_arbitrary_jev_fields(self) -> None:
        jev = JEVResult(
            success=False,
            prompt="p",
            model="free/model",
            exit_code=2,
            text="partial output",
            error="exit code 2",
        )
        result = _result(status="PARTIAL", evidence=(), payload=jev)
        self.assertIs(result.status, ResultStatus.PARTIAL)
        exit_code = getattr(result.payload, "exit_code", None)
        self.assertEqual(exit_code, 2)


# ------------------------------------------------------------------
# Architectural independence
# ------------------------------------------------------------------

class ProtocolIndependenceTests(unittest.TestCase):
    """Result/evidence logic must stay independent of JEV/providers."""

    def test_results_module_imports_governance_only(self) -> None:
        source = Path(
            inspect.getsourcefile(results_module) or ""
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
            "dataclasses",
            "enum",
            "typing",
            "protocol",
        }
        self.assertTrue(
            roots <= allowed,
            f"protocol.results imports outside governance scope: "
            f"{sorted(roots - allowed)}",
        )
        # It must reuse the TASK-002 artifact model, not a duplicate one.
        self.assertIn("protocol.artifacts", imported)


if __name__ == "__main__":
    unittest.main()
