"""MVP-004 focused tests — the backend-independent Implementer contract.

Evidence produced here (offline only — no network, model, provider, or
real backend call):

- the contract's two records describe the required **input**
  (:class:`~protocol.implementer.ImplementationTask`: project, task
  artifact with acceptance criteria and verification definition,
  execution identity, instruction) and the required **result/evidence
  output** (:class:`~protocol.implementer.ImplementationReport`:
  binding identities, explicit result status, structured
  :class:`~protocol.results.ExecutionResult` with acceptance evidence,
  detail, implementer);
- requested work and execution/result metadata are separate records:
  the task record carries no status/outcome/evidence, and the report
  record carries no instruction/authority;
- success, failure, and incomplete outcomes are representable through
  the existing V1.1 result statuses as execution/result metadata, and
  no new task or handoff lifecycle state (or enum) is invented;
- invalid/important-missing contract data fails closed: blank
  identities, invented statuses, wrong record types, unbound or
  contradictory results, ``SUCCESS`` without structured evidence, a
  task without acceptance criteria, tampered schema/record markers,
  and missing fields;
- :func:`~protocol.implementer.validate_report` fails closed unless a
  returned report answers the handed-off task and its evidence chain
  holds — and it never interprets a result or proposes completion
  (that stays with the interpretation layer and the Project Manager);
- a minimal backend adapter written *only* against the contract
  satisfies the :class:`~protocol.implementer.Implementer` interface
  and is consumable without changing the contract itself;
- the contract module imports governance scope only, names no
  provider or execution backend, holds no execution or
  permission-granting primitive, and carries no
  permission/workspace/filesystem field.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import protocol.implementer as implementer_module
from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    HandoffState,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
)
from protocol.implementer import (
    IMPLEMENTER_SCHEMA,
    IMPLEMENTER_SCHEMA_VERSION,
    Implementer,
    ImplementationReport,
    ImplementationTask,
    validate_report,
)
from protocol.results import EvidenceRecord, ExecutionResult, ResultStatus

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "AI Router / JEV"
PROJECT_VERSION = "v1.1-migration"
TASK_ID = "TASK-IMPL-001"
TASK_VERSION = "v1"
REQUIREMENT_VERSION = "v1.1"
EXECUTION_ID = "EXEC-IMPL-001"
CORRELATION_ID = "CORR-IMPL-001"
IDEMPOTENCY_KEY = "TASK-IMPL-001-v1-exec-001"
INSTRUCTION = "Implement the contract changes with focused tests."
METHOD = "Focused tests"

CRITERION_1 = "The contract is backend independent."
CRITERION_2 = "Focused tests and the full suite pass."
CRITERIA = (CRITERION_1, CRITERION_2)

#: The five closed V1.1 result statuses — the contract may reuse them,
#: never extend them.
RESULT_STATUS_NAMES = (
    "SUCCESS", "PARTIAL", "FAILURE", "BLOCKED", "NEEDS_USER",
)

#: The exact V1.1 handoff states — proving no state was invented.
HANDOFF_STATE_NAMES = (
    "PENDING", "DISPATCHED", "IN_PROGRESS", "RESULT_READY",
    "PROCESSED", "SUPERSEDED",
)


# ------------------------------------------------------------------
# Builders
# ------------------------------------------------------------------

def _project(**overrides) -> ProjectArtifact:
    base = dict(project_id=PROJECT_ID, project_version=PROJECT_VERSION)
    base.update(overrides)
    return ProjectArtifact(**base)


def _task(**overrides) -> TaskArtifact:
    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        state=TaskState.IMPLEMENTING,
        acceptance_criteria=CRITERIA,
        dependencies=(),
        verification=VerificationDefinition(
            method=METHOD, description="focused tests + full suite"
        ),
    )
    base.update(overrides)
    return TaskArtifact(**base)


def _execution(**overrides) -> ExecutionArtifact:
    base = dict(
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        idempotency_key=IDEMPOTENCY_KEY,
    )
    base.update(overrides)
    return ExecutionArtifact(**base)


def _implementation_task(**overrides) -> ImplementationTask:
    base = dict(
        project=_project(),
        task=_task(),
        execution=_execution(),
        instruction=INSTRUCTION,
    )
    base.update(overrides)
    return ImplementationTask(**base)


def _evidence(evidence_id: str, criterion: str, **overrides) -> EvidenceRecord:
    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        execution_id=EXECUTION_ID,
        requirement_version=REQUIREMENT_VERSION,
        acceptance_criterion=criterion,
        verification_method=METHOD,
        summary="focused implementer test run passed",
        reference="tests/test_implementer.py",
    )
    base.update(overrides)
    return EvidenceRecord(evidence_id=evidence_id, **base)


def _result(
    status: ResultStatus = ResultStatus.SUCCESS,
    criteria: tuple[str, ...] = CRITERIA,
    evidence: tuple[EvidenceRecord, ...] | None = None,
    **overrides,
) -> ExecutionResult:
    if evidence is None:
        evidence = tuple(
            _evidence(f"EVID-IMPL-{index:03d}", criterion)
            for index, criterion in enumerate(criteria, start=1)
        )
    base = dict(
        result_id="RESULT-IMPL-001",
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        status=status,
        acceptance_criteria=criteria,
        evidence=evidence,
        summary="implementer reported its outcome",
        detail="focused tests passed",
        payload={"checks": ["focused", "full-suite"]},
    )
    base.update(overrides)
    return ExecutionResult(**base)


def _report_fields(**overrides) -> dict:
    """Keyword arguments for one consistent SUCCESS report."""

    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        status=ResultStatus.SUCCESS,
        result=_result(),
        detail="",
        implementer="any-adapter",
    )
    base.update(overrides)
    return base


def _report(**overrides) -> ImplementationReport:
    return ImplementationReport(**_report_fields(**overrides))


def _failure_report(**overrides) -> ImplementationReport:
    """A failed execution: status only, no structured result."""

    fields = _report_fields(
        status=ResultStatus.FAILURE, result=None, detail="backend stopped"
    )
    fields.update(overrides)
    return ImplementationReport(**fields)


# ------------------------------------------------------------------
# Input: the requested work
# ------------------------------------------------------------------

class TestImplementationTaskIsTheRequiredInput:
    """The contract states exactly what an execution backend needs to
    perform one implementation task — and nothing about its outcome."""

    def test_task_carries_all_required_input_fields(self) -> None:
        task = _implementation_task()

        assert task.project == _project()
        assert task.project.project_id == PROJECT_ID
        assert task.task.task_id == TASK_ID
        assert task.task.task_version == TASK_VERSION
        assert task.task.requirement_version == REQUIREMENT_VERSION
        assert task.task.acceptance_criteria == CRITERIA
        assert task.task.verification.method == METHOD
        assert task.execution.execution_id == EXECUTION_ID
        assert task.execution.correlation_id == CORRELATION_ID
        assert task.execution.idempotency_key == IDEMPOTENCY_KEY
        assert task.instruction == INSTRUCTION

    def test_task_record_is_frozen(self) -> None:
        task = _implementation_task()
        with pytest.raises(FrozenInstanceError):
            task.instruction = "changed"  # type: ignore[misc]

    @pytest.mark.parametrize("bad", ["", "   ", None, 42])
    def test_missing_instruction_is_rejected(self, bad: object) -> None:
        with pytest.raises(ArtifactValidationError):
            _implementation_task(instruction=bad)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field", ["project", "task", "execution"]
    )
    def test_missing_artifact_field_is_rejected(self, field: str) -> None:
        with pytest.raises(ArtifactValidationError):
            _implementation_task(**{field: "not-an-artifact"})

    def test_task_without_acceptance_criteria_is_rejected(self) -> None:
        with pytest.raises(
            ArtifactValidationError, match="acceptance criterion"
        ):
            _implementation_task(task=_task(acceptance_criteria=()))

    def test_task_accepts_any_valid_state_without_inventing_one(self) -> None:
        # The contract imposes no lifecycle rule of its own: it accepts
        # V1.1 states as they are (the PM owns state transitions).
        for state in (TaskState.PENDING, TaskState.IMPLEMENTING):
            assert _implementation_task(task=_task(state=state)).task.state is state
        with pytest.raises(ArtifactValidationError):
            _task(state="NOT_A_STATE")

    def test_task_carries_no_result_or_status_metadata(self) -> None:
        fields = set(ImplementationTask.__dataclass_fields__)
        assert not fields & {
            "status", "result", "evidence", "detail", "outcome",
            "decision", "state_report",
        }

    def test_requested_work_and_result_metadata_are_distinct(self) -> None:
        task_fields = set(ImplementationTask.__dataclass_fields__)
        report_fields = set(ImplementationReport.__dataclass_fields__)
        # No field name is shared: requested work and execution/result
        # metadata are separate records of the contract.
        assert task_fields & report_fields == set()
        assert "instruction" not in report_fields
        assert not task_fields & {"status", "result"}


# ------------------------------------------------------------------
# Result/evidence: the returned metadata
# ------------------------------------------------------------------

class TestImplementationReportCarriesResultAndEvidence:
    """The report is execution/result metadata bound to the handed-off
    task and execution, with explicit success/failure/incomplete
    outcomes built only from existing V1.1 result statuses."""

    def test_success_report_carries_structured_result_and_evidence(
        self,
    ) -> None:
        report = _report()

        assert report.status is ResultStatus.SUCCESS
        assert report.is_success and not report.is_failure
        assert report.result is not None
        assert report.result.acceptance_criteria == CRITERIA
        assert len(report.result.evidence) == 2
        assert report.result.payload == {"checks": ["focused", "full-suite"]}
        assert report.task_id == TASK_ID
        assert report.execution_id == EXECUTION_ID
        assert report.implementer == "any-adapter"

    def test_failure_execution_is_representable_without_structured_result(
        self,
    ) -> None:
        report = _failure_report()

        assert report.is_failure and not report.is_success
        assert report.result is None
        assert report.detail == "backend stopped"

    @pytest.mark.parametrize(
        "status",
        [ResultStatus.PARTIAL, ResultStatus.BLOCKED,
         ResultStatus.NEEDS_USER],
    )
    def test_incomplete_outcomes_are_representable(
        self, status: ResultStatus
    ) -> None:
        report = _report(
            status=status,
            result=_result(status=status, criteria=(CRITERION_1,)),
        )

        assert report.is_incomplete
        assert not report.is_success and not report.is_failure

    def test_outcome_properties_partition_every_status(self) -> None:
        classes = {"success": 0, "failure": 0, "incomplete": 0}
        for status in ResultStatus:
            report = (
                _report()
                if status is ResultStatus.SUCCESS
                else _failure_report(status=status)
            )
            classes["success"] += report.is_success
            classes["failure"] += report.is_failure
            classes["incomplete"] += report.is_incomplete
            assert (
                report.is_success + report.is_failure + report.is_incomplete
                == 1
            ), status
        # SUCCESS, FAILURE, and the incomplete statuses are all covered.
        assert classes == {"success": 1, "failure": 1, "incomplete": 3}

    @pytest.mark.parametrize(
        "invented", ["DONE", "INCOMPLETE", "RESULT_READY", "IMPLEMENTING", ""]
    )
    def test_invented_statuses_are_rejected(self, invented: str) -> None:
        # Success/failure/incomplete reuse the closed V1.1 status set;
        # no new state may be invented by an adapter.
        with pytest.raises(ArtifactValidationError):
            _report(status=invented)

    @pytest.mark.parametrize(
        "field",
        [
            "task_id", "task_version", "requirement_version",
            "execution_id", "correlation_id",
        ],
    )
    def test_missing_binding_identity_is_rejected(self, field: str) -> None:
        for blank in ("", "   "):
            with pytest.raises(ArtifactValidationError):
                _report(**{field: blank})

    @pytest.mark.parametrize("wrong", ["not-a-result", 7, {"status": "SUCCESS"}])
    def test_non_result_payload_is_rejected(self, wrong: object) -> None:
        with pytest.raises(ArtifactValidationError):
            _report(status=ResultStatus.FAILURE, result=wrong)  # type: ignore[arg-type]

    def test_result_bound_to_another_execution_is_rejected(self) -> None:
        foreign = _result(
            status=ResultStatus.FAILURE,
            execution_id="EXEC-OTHER-001",
            evidence=(),
        )
        with pytest.raises(ArtifactValidationError, match="not bound"):
            _report(status=ResultStatus.FAILURE, result=foreign)

    def test_status_and_structured_result_must_agree(self) -> None:
        with pytest.raises(ArtifactValidationError, match="disagrees"):
            _report(status=ResultStatus.PARTIAL, result=_result())
        with pytest.raises(ArtifactValidationError, match="disagrees"):
            _failure_report(result=_result())

    def test_success_report_requires_a_structured_result(self) -> None:
        with pytest.raises(
            ArtifactValidationError, match="must carry the structured"
        ):
            _report(result=None)

    def test_success_report_requires_evidence(self) -> None:
        # The nested V1.1 result refuses a completion claim with no
        # acceptance evidence, so the contract can never carry one.
        with pytest.raises(ArtifactValidationError):
            _report(
                result=_result(
                    criteria=(), evidence=(),
                )
            )

    @pytest.mark.parametrize("field", ["detail", "implementer"])
    def test_non_string_metadata_is_rejected(self, field: str) -> None:
        with pytest.raises(ArtifactValidationError):
            _report(**{field: 42})

    def test_report_is_frozen(self) -> None:
        report = _report()
        with pytest.raises(FrozenInstanceError):
            report.status = ResultStatus.FAILURE  # type: ignore[misc]

    def test_report_carries_no_instruction_or_authority(self) -> None:
        fields = set(ImplementationReport.__dataclass_fields__)
        assert not fields & {
            "instruction", "approval", "decision", "transition",
            "policy", "permission",
        }


# ------------------------------------------------------------------
# Consumption check: the report must answer the task
# ------------------------------------------------------------------

class TestValidateReport:
    """The PM/Orchestrator side can fail closed on a returned report
    without interpreting it."""

    def test_report_for_handed_off_task_validates(self) -> None:
        validate_report(_report(), _implementation_task())

    def test_failure_report_without_structured_result_validates(
        self,
    ) -> None:
        validate_report(_failure_report(), _implementation_task())

    def test_report_for_a_different_task_is_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError, match="answers task"):
            validate_report(
                _failure_report(task_id="TASK-OTHER"),
                _implementation_task(),
            )

    def test_report_for_a_different_execution_is_rejected(self) -> None:
        foreign = _result(
            status=ResultStatus.FAILURE,
            execution_id="EXEC-OTHER",
            evidence=(),
        )
        with pytest.raises(
            ArtifactValidationError, match="handed-off execution"
        ):
            validate_report(
                _report(status=ResultStatus.FAILURE,
                        execution_id="EXEC-OTHER", result=foreign),
                _implementation_task(),
            )

    def test_evidence_outside_the_task_is_rejected(self) -> None:
        # The structured result names an acceptance criterion the
        # handed-off task never declared: the evidence chain breaks.
        alien = _evidence("EVID-IMPL-ALIEN", "An undeclared criterion.")
        result = _result(
            criteria=(CRITERION_1, "An undeclared criterion."),
            evidence=(_evidence("EVID-IMPL-001", CRITERION_1), alien),
        )
        report = _report(result=result)

        with pytest.raises(
            ArtifactValidationError, match="not defined by"
        ):
            validate_report(report, _implementation_task())

    @pytest.mark.parametrize("report", ["not-a-report", None, 7])
    def test_wrong_report_type_is_rejected(self, report: object) -> None:
        with pytest.raises(ArtifactValidationError, match="report"):
            validate_report(report, _implementation_task())  # type: ignore[arg-type]

    def test_wrong_task_type_is_rejected(self) -> None:
        with pytest.raises(ArtifactValidationError, match="task"):
            validate_report(_report(), "not-a-task")  # type: ignore[arg-type]

    def test_contract_never_decides_completion(self) -> None:
        # A success claim whose structured result states (and evidences)
        # only part of the task's criteria passes the contract check:
        # deciding completion belongs to the interpretation layer and
        # the Project Manager, never to the Implementer contract.
        partial = _result(criteria=(CRITERION_1,))
        report = _report(result=partial)

        validate_report(report, _implementation_task())
        assert report.result.covered_criteria == (CRITERION_1,)
        assert set(CRITERIA) - set(report.result.covered_criteria)


# ------------------------------------------------------------------
# The interface a backend adapter satisfies
# ------------------------------------------------------------------

class TestImplementerInterface:
    """A future adapter conforms by implementing ``execute`` over the
    contract records — the contract itself never changes."""

    class FakeImplementer:
        """A backend adapter written only against the contract."""

        def __init__(self) -> None:
            self.received: list[ImplementationTask] = []

        def execute(
            self, task: ImplementationTask
        ) -> ImplementationReport:
            self.received.append(task)
            if task.instruction == "fail":
                return _failure_report()
            return _report()

    def test_conforming_backend_satisfies_the_interface(self) -> None:
        assert isinstance(self.FakeImplementer(), Implementer)

    def test_object_without_execute_does_not_satisfy_it(self) -> None:
        assert not isinstance(object(), Implementer)

    def test_execute_receives_the_task_and_returns_a_consumable_report(
        self,
    ) -> None:
        backend = self.FakeImplementer()
        task = _implementation_task()

        report = backend.execute(task)

        # The adapter received exactly the requested work…
        assert backend.received == [task]
        assert backend.received[0].instruction == INSTRUCTION
        # …and returned result metadata the PM side can consume
        # without touching the contract.
        assert isinstance(report, ImplementationReport)
        validate_report(report, task)

    def test_failure_path_is_consumable_too(self) -> None:
        backend = self.FakeImplementer()
        task = _implementation_task(instruction="fail")

        report = backend.execute(task)

        validate_report(report, task)
        assert report.is_failure

    def test_non_report_return_is_rejected_at_consumption(self) -> None:
        with pytest.raises(ArtifactValidationError, match="report"):
            validate_report("backend-output", _implementation_task())


# ------------------------------------------------------------------
# Machine-readable records
# ------------------------------------------------------------------

class TestMachineReadableContract:
    """Both records serialize to self-describing, schema-checked
    machine-readable form."""

    def test_task_record_round_trips_through_dict(self) -> None:
        task = _implementation_task()

        record = task.to_dict()

        assert record["schema"] == IMPLEMENTER_SCHEMA
        assert record["schema_version"] == IMPLEMENTER_SCHEMA_VERSION
        assert record["record"] == "implementation-task"
        assert ImplementationTask.from_dict(record) == task

    def test_task_record_round_trips_through_json(self) -> None:
        task = _implementation_task()
        assert ImplementationTask.from_json(task.to_json()) == task

    def test_report_record_round_trips_with_result_and_evidence(
        self,
    ) -> None:
        report = _report()

        record = report.to_dict()

        assert record["schema"] == IMPLEMENTER_SCHEMA
        assert record["record"] == "implementation-report"
        rebuilt = ImplementationReport.from_dict(record)
        assert rebuilt == report
        assert rebuilt.result is not None
        assert rebuilt.result.evidence == report.result.evidence
        assert rebuilt.result.payload == {"checks": ["focused", "full-suite"]}

    def test_report_record_round_trips_through_json(self) -> None:
        report = _report()
        assert ImplementationReport.from_json(report.to_json()) == report

    def test_report_without_structured_result_round_trips(self) -> None:
        report = _failure_report()
        assert ImplementationReport.from_json(report.to_json()) == report

    @pytest.mark.parametrize("record_type", ["task", "report"])
    def test_foreign_schema_markers_are_rejected(self, record_type: str) -> None:
        task, report = _implementation_task().to_dict(), _report().to_dict()
        cls, record = (
            (ImplementationTask, task)
            if record_type == "task"
            else (ImplementationReport, report)
        )

        broken = dict(record, schema="some.other.schema")
        with pytest.raises(ArtifactValidationError, match="schema"):
            cls.from_dict(broken)

        broken = dict(record, schema_version="999")
        with pytest.raises(ArtifactValidationError, match="schema version"):
            cls.from_dict(broken)

        other = (
            "implementation-report"
            if record_type == "task"
            else "implementation-task"
        )
        broken = dict(record, record=other)
        with pytest.raises(ArtifactValidationError, match="record"):
            cls.from_dict(broken)

    @pytest.mark.parametrize(
        "field", ["schema", "schema_version", "record", "task_id"]
    )
    def test_missing_report_field_is_rejected(self, field: str) -> None:
        record = _report().to_dict()
        del record[field]
        with pytest.raises(ArtifactValidationError):
            ImplementationReport.from_dict(record)

    def test_missing_task_instruction_is_rejected(self) -> None:
        record = _implementation_task().to_dict()
        del record["instruction"]
        with pytest.raises(ArtifactValidationError, match="instruction"):
            ImplementationTask.from_dict(record)

    def test_missing_nested_result_identity_is_rejected(self) -> None:
        record = _report().to_dict()
        del record["result"]["result_id"]
        with pytest.raises(ArtifactValidationError, match="result_id"):
            ImplementationReport.from_dict(record)

    def test_bare_string_acceptance_criteria_is_rejected(self) -> None:
        record = _implementation_task().to_dict()
        record["task"]["acceptance_criteria"] = CRITERION_1
        with pytest.raises(ArtifactValidationError, match="acceptance_criteria"):
            ImplementationTask.from_dict(record)

    @pytest.mark.parametrize("record_type", ["task", "report"])
    def test_non_object_and_broken_json_are_rejected(
        self, record_type: str
    ) -> None:
        cls = (
            ImplementationTask if record_type == "task" else ImplementationReport
        )
        with pytest.raises(ArtifactValidationError, match="object"):
            cls.from_dict("not-an-object")
        with pytest.raises(ArtifactValidationError, match="JSON"):
            cls.from_json("{not json")

    def test_report_with_non_object_result_is_rejected(self) -> None:
        record = _report().to_dict()
        record["result"] = "a bare string"
        with pytest.raises(ArtifactValidationError, match="result"):
            ImplementationReport.from_dict(record)

    def test_recorded_status_stays_in_the_closed_status_set(self) -> None:
        record = json.loads(_report().to_json())
        record["status"] = "SOMETHING_NEW"
        with pytest.raises(ArtifactValidationError, match="status"):
            ImplementationReport.from_dict(record)


# ------------------------------------------------------------------
# Backend/provider agnosticism (source-level, like the V1.1 boundary
# tests)
# ------------------------------------------------------------------

def _contract_source() -> str:
    path = Path(inspect.getsourcefile(implementer_module) or "")
    return path.read_text(encoding="utf-8")


def _imported_roots(text: str) -> set[str]:
    tree = ast.parse(text)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    return {name.split(".")[0] for name in imported}


class TestContractIsBackendAndProviderAgnostic:
    """The contract embeds no backend, provider, execution, or
    permission assumption — enforced at source level, not by
    convention."""

    def test_module_imports_governance_scope_only(self) -> None:
        roots = _imported_roots(_contract_source())
        assert roots <= {
            "__future__",
            "dataclasses",
            "json",
            "typing",
            "protocol",
        }, sorted(roots)

    def test_module_names_no_provider_or_execution_backend(self) -> None:
        lowered = _contract_source().lower()
        banned = (
            "opencode",
            "codex",
            "openrouter",
            "anthropic",
            "ollama",
            "gemini",
        )
        offenders = [token for token in banned if token in lowered]
        assert offenders == [], f"contract names a backend: {offenders}"

    def test_module_holds_no_execution_or_permission_primitive(self) -> None:
        lowered = _contract_source().lower()
        banned = (
            "subprocess",
            "socket",
            "urllib",
            "http",
            "requests",
            "keyring",
            "getenv",
            "system(",
            "popen",
            "eval(",
            "exec(",
            "__import__",
            "importlib",
        )
        offenders = [token for token in banned if token in lowered]
        assert offenders == [], f"contract holds a forbidden primitive: {offenders}"

    def test_module_reaches_no_pm_decision_model(self) -> None:
        source = _contract_source()
        assert "PMDecision" not in source
        assert "PMInterpreter" not in source

    @pytest.mark.parametrize(
        "cls",
        [ImplementationTask, ImplementationReport],
    )
    def test_records_carry_no_permission_workspace_or_cli_field(
        self, cls
    ) -> None:
        fields = set(cls.__dataclass_fields__)
        banned = {
            "policy", "permission", "permissions", "credential",
            "credentials", "token", "workspace", "shell", "backend",
            "network", "command", "workdir", "cwd", "path", "model",
        }
        assert not fields & banned, sorted(fields & banned)

    def test_contract_defines_no_new_lifecycle_state(self) -> None:
        tree = ast.parse(_contract_source())
        enum_bases = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = {ast.unparse(base) for base in node.bases}
                enum_bases.extend(bases & {"Enum", "StrEnum", "IntEnum"})
        assert enum_bases == [], f"contract defines an enum: {enum_bases}"

        # The contract only reuses the closed V1.1 vocabularies.
        assert tuple(m.name for m in ResultStatus) == RESULT_STATUS_NAMES
        assert tuple(m.name for m in HandoffState) == HANDOFF_STATE_NAMES

        report = _report()
        assert type(report.status) is ResultStatus
        assert report.status is ResultStatus.SUCCESS


if __name__ == "__main__":
    pytest.main([__file__])
