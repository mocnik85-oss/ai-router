"""Interrogator AI-Assisted Project Lifecycle Protocol V1.1 — the
backend-independent Implementer contract (MVP-004).

The contract is the small, machine-readable interface between the
PM/Orchestrator side and *any* implementation backend:

    ImplementationTask    the requested work handed to a backend
            |
            |  Implementer.execute(task)
            v
    ImplementationReport  the execution/result metadata handed back

- :class:`ImplementationTask` describes **the input required to
  execute an implementation task**: the project identity, the
  authoritative :class:`~protocol.artifacts.TaskArtifact` (identity,
  requirement version, acceptance criteria, dependencies, and
  verification definition), the :class:`~protocol.artifacts.ExecutionArtifact`
  identifying this run, and the free-text ``instruction`` stating the
  requested work.  It carries no outcome — requested work and
  execution/result metadata are deliberately separate records.
- :class:`ImplementationReport` describes **the result/evidence an
  implementation backend returns**: the binding identities, an explicit
  result status, the narrative ``detail``, the ``implementer`` name
  that produced it, and — when the backend produced one — the
  structured :class:`~protocol.results.ExecutionResult` carrying the
  acceptance evidence the later Tester and Reviewer stages consume.
- :class:`Implementer` is the structural interface (``execute``) that
  any backend adapter satisfies.
- :func:`validate_report` is the fail-closed check that a returned
  report answers the task that was handed off and that its evidence
  chain holds.

Outcomes reuse the existing :class:`~protocol.results.ResultStatus`
instead of inventing anything new: ``SUCCESS`` is a successful
execution, ``FAILURE`` a failed one, and ``PARTIAL``/``BLOCKED``/
``NEEDS_USER`` an incomplete one (see :attr:`ImplementationReport.is_success`,
:attr:`ImplementationReport.is_failure`, and
:attr:`ImplementationReport.is_incomplete`, which partition the closed
status set).  **No V1.1 task or handoff lifecycle state is defined,
added, or changed here**, and this module never interprets a result —
interpretation stays in ``protocol.interpretation`` and applying a
decision stays with the Project Manager.

Agnosticism: this module depends only on the standard library plus
:mod:`protocol.artifacts` and :mod:`protocol.results`.  It embeds no
provider, coding CLI, model, operating-system, repository, filesystem,
network, credential, or permission assumption, and it holds no
workspace or project-specific requirement: project-specific
requirements belong to the project artifacts/configuration, not to this
global contract.  A future backend adapter conforms by implementing
:meth:`Implementer.execute` over these records; the contract itself
does not change for any particular backend.

Deliberately out of scope here (later MVP tasks): any concrete
adapter, batch scheduling, controlled writes, workspace enforcement,
network policy, recovery, Tester, Reviewer, Git operations, and
end-to-end orchestration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    ProjectArtifact,
    TaskArtifact,
    VerificationDefinition,
    _coerce_state,
    _require_identity,
)
from protocol.results import (
    EvidenceRecord,
    ExecutionResult,
    ResultStatus,
    validate_traceability,
)

#: Schema marker stored with every machine-readable contract record.
IMPLEMENTER_SCHEMA = "ai-router.protocol.implementer"

#: Schema version of the records written by this MVP.
IMPLEMENTER_SCHEMA_VERSION = "1"

#: Record discriminator of the two contract records.
_TASK_RECORD = "implementation-task"
_REPORT_RECORD = "implementation-report"


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------

def _require_text(value: object, field_name: str) -> None:
    """Reject non-string narrative fields."""

    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a string")


def _text(data: Mapping[str, Any], key: str) -> str:
    """Read one required string field of a contract record."""

    if key not in data:
        raise ArtifactValidationError(
            f"implementation record is missing {key!r}"
        )
    value = data[key]
    if not isinstance(value, str):
        raise ArtifactValidationError(
            f"implementation record field {key!r} must be a string"
        )
    return value


def _entries(value: object, key: str) -> tuple[str, ...]:
    """Read one list-of-strings field, refusing a single bare string."""

    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ArtifactValidationError(
            f"implementation record field {key!r} must be a list of strings"
        )
    return tuple(value)


def _check_record(data: object, record: str) -> Mapping[str, Any]:
    """Validate the schema marker of one contract record.

    Unknown schemas, unknown schema versions, and foreign record kinds
    are all rejected: a tampered or foreign record never becomes a
    loadable implementation record.
    """

    if not isinstance(data, Mapping):
        raise ArtifactValidationError("implementation record must be an object")

    schema = data.get("schema")
    if schema != IMPLEMENTER_SCHEMA:
        raise ArtifactValidationError(
            f"unsupported implementer schema: {schema!r}"
        )
    version = str(data.get("schema_version", ""))
    if version != IMPLEMENTER_SCHEMA_VERSION:
        raise ArtifactValidationError(
            f"unsupported implementer schema version: {version!r}"
        )
    kind = data.get("record")
    if kind != record:
        raise ArtifactValidationError(
            f"expected a {record!r} record, got {kind!r}"
        )
    return data


# ------------------------------------------------------------------
# Nested V1.1 artifact records (serialization only — no parallel model)
# ------------------------------------------------------------------

def _project_to_dict(project: ProjectArtifact) -> dict:
    return {
        "project_id": project.project_id,
        "project_version": project.project_version,
    }


def _project_from_dict(data: object) -> ProjectArtifact:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("project must be an object")
    return ProjectArtifact(
        project_id=data.get("project_id", ""),
        project_version=data.get("project_version", ""),
    )


def _task_to_dict(task: TaskArtifact) -> dict:
    return {
        "task_id": task.task_id,
        "task_version": task.task_version,
        "requirement_version": task.requirement_version,
        "state": str(task.state),
        "acceptance_criteria": list(task.acceptance_criteria),
        "dependencies": list(task.dependencies),
        "verification": (
            None
            if task.verification is None
            else {
                "method": task.verification.method,
                "description": task.verification.description,
            }
        ),
    }


def _task_from_dict(data: object) -> TaskArtifact:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("task must be an object")
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
        acceptance_criteria=_entries(
            data.get("acceptance_criteria"), "acceptance_criteria"
        ),
        dependencies=_entries(data.get("dependencies"), "dependencies"),
        verification=verification,
    )


def _execution_to_dict(execution: ExecutionArtifact) -> dict:
    return {
        "execution_id": execution.execution_id,
        "correlation_id": execution.correlation_id,
        "idempotency_key": execution.idempotency_key,
    }


def _execution_from_dict(data: object) -> ExecutionArtifact:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("execution must be an object")
    return ExecutionArtifact(
        execution_id=data.get("execution_id", ""),
        correlation_id=data.get("correlation_id", ""),
        idempotency_key=data.get("idempotency_key", ""),
    )


def _evidence_to_dict(item: EvidenceRecord) -> dict:
    return {
        "evidence_id": item.evidence_id,
        "task_id": item.task_id,
        "task_version": item.task_version,
        "execution_id": item.execution_id,
        "requirement_version": item.requirement_version,
        "acceptance_criterion": item.acceptance_criterion,
        "verification_method": item.verification_method,
        "summary": item.summary,
        "reference": item.reference,
    }


def _evidence_from_dict(data: object) -> EvidenceRecord:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("evidence entry must be an object")
    return EvidenceRecord(
        evidence_id=_text(data, "evidence_id"),
        task_id=_text(data, "task_id"),
        task_version=_text(data, "task_version"),
        execution_id=_text(data, "execution_id"),
        requirement_version=_text(data, "requirement_version"),
        acceptance_criterion=_text(data, "acceptance_criterion"),
        verification_method=_text(data, "verification_method"),
        summary=_text(data, "summary"),
        reference=data.get("reference", ""),
    )


def _result_to_dict(result: ExecutionResult) -> dict:
    return {
        "result_id": result.result_id,
        "task_id": result.task_id,
        "task_version": result.task_version,
        "requirement_version": result.requirement_version,
        "execution_id": result.execution_id,
        "correlation_id": result.correlation_id,
        "status": str(result.status),
        "acceptance_criteria": list(result.acceptance_criteria),
        "evidence": [_evidence_to_dict(item) for item in result.evidence],
        "summary": result.summary,
        "detail": result.detail,
        "payload": result.payload,
    }


def _result_from_dict(data: object) -> ExecutionResult:
    if not isinstance(data, Mapping):
        raise ArtifactValidationError("result must be an object")
    evidence_data = data.get("evidence", ())
    if isinstance(evidence_data, str) or not isinstance(
        evidence_data, (tuple, list)
    ):
        raise ArtifactValidationError(
            "evidence must be a list of evidence objects"
        )
    return ExecutionResult(
        result_id=_text(data, "result_id"),
        task_id=_text(data, "task_id"),
        task_version=_text(data, "task_version"),
        requirement_version=_text(data, "requirement_version"),
        execution_id=_text(data, "execution_id"),
        correlation_id=_text(data, "correlation_id"),
        status=data.get("status", ""),
        acceptance_criteria=_entries(
            data.get("acceptance_criteria"), "acceptance_criteria"
        ),
        evidence=tuple(_evidence_from_dict(item) for item in evidence_data),
        summary=data.get("summary", ""),
        detail=data.get("detail", ""),
        payload=data.get("payload"),
    )


# ------------------------------------------------------------------
# Contract records
# ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ImplementationTask:
    """The requested work handed to an implementation backend.

    This is the complete, backend-independent **input** required to
    execute one implementation task.  Every field is required:

    - ``project`` — the :class:`~protocol.artifacts.ProjectArtifact`
      the work belongs to (identity and project version);
    - ``task`` — the authoritative
      :class:`~protocol.artifacts.TaskArtifact`: task identity,
      task version, requirement version, acceptance criteria,
      dependencies, and verification definition.  At least one
      acceptance criterion must be stated, so the returned result can
      be verified;
    - ``execution`` — the :class:`~protocol.artifacts.ExecutionArtifact`
      (execution identity, correlation ID, idempotency key) that
      identifies this run and that every returned evidence record must
      cite;
    - ``instruction`` — the non-empty text describing what the backend
      is asked to do.

    The record is *requested work only*: it carries no status, no
    outcome, no evidence, and no permission of any kind.  Where the
    work happens and which project-specific constraints apply is not
    decided here — those belong to the project artifacts/configuration
    that accompany the task, never to this contract.
    """

    project: ProjectArtifact
    task: TaskArtifact
    execution: ExecutionArtifact
    instruction: str

    def __post_init__(self) -> None:
        if not isinstance(self.project, ProjectArtifact):
            raise ArtifactValidationError(
                "project must be a ProjectArtifact"
            )
        if not isinstance(self.task, TaskArtifact):
            raise ArtifactValidationError("task must be a TaskArtifact")
        if not isinstance(self.execution, ExecutionArtifact):
            raise ArtifactValidationError(
                "execution must be an ExecutionArtifact"
            )
        _require_identity(self.instruction, "instruction")
        if not self.task.acceptance_criteria:
            raise ArtifactValidationError(
                "an implementation task requires at least one acceptance "
                "criterion so its result can be verified"
            )

    def to_dict(self) -> dict:
        """Machine-readable representation of the requested work."""

        return {
            "schema": IMPLEMENTER_SCHEMA,
            "schema_version": IMPLEMENTER_SCHEMA_VERSION,
            "record": _TASK_RECORD,
            "project": _project_to_dict(self.project),
            "task": _task_to_dict(self.task),
            "execution": _execution_to_dict(self.execution),
            "instruction": self.instruction,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the requested work as JSON text."""

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: object) -> "ImplementationTask":
        """Rebuild requested work from its machine-readable record."""

        record = _check_record(data, _TASK_RECORD)
        return cls(
            project=_project_from_dict(record.get("project")),
            task=_task_from_dict(record.get("task")),
            execution=_execution_from_dict(record.get("execution")),
            instruction=_text(record, "instruction"),
        )

    @classmethod
    def from_json(cls, text: str) -> "ImplementationTask":
        """Rebuild requested work from JSON text."""

        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                f"implementation record is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)


@dataclass(frozen=True, slots=True)
class ImplementationReport:
    """Execution/result metadata returned for one implementation task.

    This is the **result/evidence** side of the contract.  It is
    execution metadata, never requested work and never a lifecycle
    transition:

    - ``task_id`` / ``task_version`` / ``requirement_version`` /
      ``execution_id`` / ``correlation_id`` bind the report to exactly
      the task and execution that were handed off (these identities,
      and only the result metadata, are held here — the requested work
      stays on the :class:`ImplementationTask`);
    - ``status`` is the explicit V1.1
      :class:`~protocol.results.ResultStatus`, so success
      (``SUCCESS``), failure (``FAILURE``), and incomplete
      (``PARTIAL``/``BLOCKED``/``NEEDS_USER``) executions are all
      representable **without inventing a new state** of any kind;
    - ``result`` optionally carries the structured
      :class:`~protocol.results.ExecutionResult` — acceptance criteria,
      evidence records, summary/detail, and the backend's raw payload —
      which is what the later Tester and Reviewer stages consume.
      When present it must be bound to these same identities and must
      report the same status as the report itself;
    - ``detail`` is free-form narrative (for example why an execution
      stopped), and ``implementer`` names the backend that produced
      this report, if it chooses to say.

    A ``SUCCESS`` report must carry its structured result, so a success
    claim can never arrive without the acceptance evidence enforced by
    :class:`~protocol.results.ExecutionResult`.  A failed or incomplete
    execution may report ``result=None`` when the backend never
    produced a structured result; ``detail`` then carries the
    narrative.

    This record reports raw execution state only.  Interpreting it into
    a decision, and any authoritative state change, remain outside this
    contract.
    """

    task_id: str
    task_version: str
    requirement_version: str
    execution_id: str
    correlation_id: str
    status: ResultStatus | str
    result: ExecutionResult | None = None
    detail: str = ""
    implementer: str = ""

    def __post_init__(self) -> None:
        for field in (
            "task_id",
            "task_version",
            "requirement_version",
            "execution_id",
            "correlation_id",
        ):
            _require_identity(getattr(self, field), field)

        object.__setattr__(
            self, "status", _coerce_state(self.status, ResultStatus, "status")
        )
        _require_text(self.detail, "detail")
        _require_text(self.implementer, "implementer")

        if self.result is None:
            if self.status is ResultStatus.SUCCESS:
                raise ArtifactValidationError(
                    "a SUCCESS implementation report must carry the "
                    "structured result with its acceptance evidence"
                )
            return

        if not isinstance(self.result, ExecutionResult):
            raise ArtifactValidationError(
                "result must be an ExecutionResult or None"
            )
        if (
            self.result.task_id != self.task_id
            or self.result.task_version != self.task_version
            or self.result.requirement_version != self.requirement_version
            or self.result.execution_id != self.execution_id
            or self.result.correlation_id != self.correlation_id
        ):
            raise ArtifactValidationError(
                f"result {self.result.result_id!r} is not bound to task "
                f"{self.task_id!r} version {self.task_version!r} execution "
                f"{self.execution_id!r}/correlation "
                f"{self.correlation_id!r}"
            )
        if self.result.status is not self.status:
            raise ArtifactValidationError(
                f"report status {self.status} disagrees with the status "
                f"{self.result.status} of result {self.result.result_id!r}"
            )

    # ------------------------------------------------------------------
    # Outcome classes (existing result statuses — no new states)
    # ------------------------------------------------------------------

    @property
    def is_success(self) -> bool:
        """True when the execution succeeded (``SUCCESS``)."""

        return self.status is ResultStatus.SUCCESS

    @property
    def is_failure(self) -> bool:
        """True when the execution failed (``FAILURE``)."""

        return self.status is ResultStatus.FAILURE

    @property
    def is_incomplete(self) -> bool:
        """True when the execution is incomplete but not a failure.

        Covers ``PARTIAL`` (some work verified), ``BLOCKED``, and
        ``NEEDS_USER``.  Together with :attr:`is_success` and
        :attr:`is_failure` this partitions every representable status.
        """

        return self.status in {
            ResultStatus.PARTIAL,
            ResultStatus.BLOCKED,
            ResultStatus.NEEDS_USER,
        }

    def to_dict(self) -> dict:
        """Machine-readable representation of the whole report."""

        return {
            "schema": IMPLEMENTER_SCHEMA,
            "schema_version": IMPLEMENTER_SCHEMA_VERSION,
            "record": _REPORT_RECORD,
            "task_id": self.task_id,
            "task_version": self.task_version,
            "requirement_version": self.requirement_version,
            "execution_id": self.execution_id,
            "correlation_id": self.correlation_id,
            "status": str(self.status),
            "result": (
                None if self.result is None else _result_to_dict(self.result)
            ),
            "detail": self.detail,
            "implementer": self.implementer,
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialize the report as JSON text.

        The raw ``payload`` of a structured result must be
        JSON-representable for text serialization; :meth:`to_dict`
        always works unchanged.
        """

        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: object) -> "ImplementationReport":
        """Rebuild a report from its machine-readable record."""

        record = _check_record(data, _REPORT_RECORD)
        result_data = record.get("result")
        if result_data is None:
            result = None
        elif isinstance(result_data, Mapping):
            result = _result_from_dict(result_data)
        else:
            raise ArtifactValidationError("result must be an object or null")
        return cls(
            task_id=_text(record, "task_id"),
            task_version=_text(record, "task_version"),
            requirement_version=_text(record, "requirement_version"),
            execution_id=_text(record, "execution_id"),
            correlation_id=_text(record, "correlation_id"),
            status=record.get("status", ""),
            result=result,
            detail=record.get("detail", ""),
            implementer=record.get("implementer", ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "ImplementationReport":
        """Rebuild a report from JSON text."""

        try:
            data = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise ArtifactValidationError(
                f"implementation record is not valid JSON: {exc}"
            ) from exc
        return cls.from_dict(data)


# ------------------------------------------------------------------
# The interface and its fail-closed consumption check
# ------------------------------------------------------------------

@runtime_checkable
class Implementer(Protocol):
    """The backend-independent interface of an implementation backend.

    Governance code depends only on this interface: it calls
    :meth:`execute` with the requested work and receives the
    execution/result metadata back, and it never inspects *how* the
    backend performs the work.  A backend adapter conforms by
    implementing this single method over
    :class:`ImplementationTask` and :class:`ImplementationReport` —
    the contract changes for no provider, coding CLI, model, operating
    system, repository, or project.
    """

    def execute(self, task: ImplementationTask) -> ImplementationReport:
        """Perform *task*'s requested work and report its outcome."""
        ...


def validate_report(report: object, task: ImplementationTask) -> None:
    """Fail closed unless *report* answers *task* and its chain holds.

    Checks, in order:

    - *task* is an :class:`ImplementationTask` and *report* is an
      :class:`ImplementationReport` (anything else is API misuse and
      raises :class:`~protocol.artifacts.ArtifactValidationError`);
    - the report is bound to exactly the handed-off task identity,
      task version, requirement version, execution identity, and
      correlation ID;
    - when the report carries a structured result, the full V1.1
      evidence chain holds against the handed-off task and execution
      (:func:`~protocol.results.validate_traceability`), so the
      acceptance criteria and verification method it reports are the
      ones the task declares.

    A report carrying no structured result (a failed or incomplete
    execution the backend never structured) still passes the binding
    check.  This function never interprets the result and never
    proposes completion — that remains the interpretation layer and
    the Project Manager.
    """

    if not isinstance(task, ImplementationTask):
        raise ArtifactValidationError(
            "task must be an ImplementationTask"
        )
    if not isinstance(report, ImplementationReport):
        raise ArtifactValidationError(
            "report must be an ImplementationReport"
        )

    if (
        report.task_id != task.task.task_id
        or report.task_version != task.task.task_version
        or report.requirement_version != task.task.requirement_version
    ):
        raise ArtifactValidationError(
            f"report answers task {report.task_id!r} version "
            f"{report.task_version!r} requirement "
            f"{report.requirement_version!r}, not "
            f"{task.task.task_id!r} version {task.task.task_version!r} "
            f"requirement {task.task.requirement_version!r}"
        )

    if (
        report.execution_id != task.execution.execution_id
        or report.correlation_id != task.execution.correlation_id
    ):
        raise ArtifactValidationError(
            f"report cites execution {report.execution_id!r}/correlation "
            f"{report.correlation_id!r}, not the handed-off execution "
            f"{task.execution.execution_id!r}/"
            f"{task.execution.correlation_id!r}"
        )

    if report.result is not None:
        validate_traceability(report.result, task.task, task.execution)
