"""Interrogator AI-Assisted Project Lifecycle Protocol V1.1 — structured
result and evidence model.

Protocol V1.1 requires execution outcomes to be traceable through the
evidence chain:

    Requirement -> Acceptance Criterion -> Verification Method
                -> Evidence -> Result

- :class:`ResultStatus` represents the explicit result status: SUCCESS,
  PARTIAL, FAILURE, BLOCKED, NEEDS_USER.  No other status may be
  invented.
- :class:`EvidenceRecord` represents one piece of evidence bound to the
  task/version, execution, requirement version, acceptance criterion,
  and verification method that produced it.
- :class:`ExecutionResult` represents a structured execution result: it
  binds an explicit status to the acceptance criteria it reports against
  and to the evidence supporting them.  A result cannot claim
  completion (SUCCESS) without evidence covering every stated
  acceptance criterion.

Required identity/version fields are validated at construction time by
reusing the TASK-002 helpers from :mod:`protocol.artifacts`, so the
result/evidence model extends the authoritative artifact model instead
of redesigning it.

Like :mod:`protocol.lease`, this module depends only on
``protocol.artifacts`` plus the standard library — never on
``router``/JEV, a provider, or any paid model/API.  Existing JEV
results remain representable through the generic ``payload`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    TaskArtifact,
    _coerce_state,
    _require_entries,
    _require_identity,
)


class ResultStatus(StrEnum):
    """The explicit V1.1 result statuses. No other statuses are permitted."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"
    BLOCKED = "BLOCKED"
    NEEDS_USER = "NEEDS_USER"


# ------------------------------------------------------------------
# Validation helpers
# ------------------------------------------------------------------

def _coerce_evidence(value: object) -> tuple["EvidenceRecord", ...]:
    """Validate a sequence of :class:`EvidenceRecord` entries.

    A single record (or any non-sequence) is rejected exactly like the
    acceptance-criteria/dependency sequences in :mod:`protocol.artifacts`,
    and each evidence identity must appear at most once per result.
    """

    if isinstance(value, str) or not isinstance(value, (tuple, list)):
        raise ArtifactValidationError(
            "evidence must be a sequence of EvidenceRecord, not a single value"
        )

    entries = tuple(value)
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, EvidenceRecord):
            raise ArtifactValidationError(
                f"evidence contains a non-EvidenceRecord entry: {entry!r}"
            )
        if entry.evidence_id in seen:
            raise ArtifactValidationError(
                f"duplicate evidence_id {entry.evidence_id!r} within one result"
            )
        seen.add(entry.evidence_id)
    return entries


def _require_text(value: object, field_name: str) -> None:
    """Reject non-string narrative fields."""

    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a string")


# ------------------------------------------------------------------
# Artifacts of the result/evidence model
# ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One piece of evidence in the V1.1 evidence chain.

    The record carries every link of the chain for one observation:

    - Requirement: ``requirement_version`` (with ``task_id``/``task_version``)
    - Acceptance Criterion: ``acceptance_criterion``
    - Verification Method: ``verification_method``
    - Evidence: this record (``evidence_id`` and ``summary``)
    - Result: via ``execution_id`` binding to the
      :class:`ExecutionResult` that cites it

    ``reference`` optionally points at the concrete artifact of the
    verification (test run, compile output, inspection note).
    """

    evidence_id: str
    task_id: str
    task_version: str
    execution_id: str
    requirement_version: str
    acceptance_criterion: str
    verification_method: str
    summary: str
    reference: str = ""

    def __post_init__(self) -> None:
        for field in (
            "evidence_id",
            "task_id",
            "task_version",
            "execution_id",
            "requirement_version",
            "acceptance_criterion",
            "verification_method",
            "summary",
        ):
            _require_identity(getattr(self, field), field)
        _require_text(self.reference, "reference")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Structured V1.1 execution result bound to its acceptance evidence.

    Identity/version fields (``result_id``, ``task_id``,
    ``task_version``, ``requirement_version``, ``execution_id``,
    ``correlation_id``), an explicit :class:`ResultStatus`, the
    acceptance criteria this result reports against, and the evidence
    records supporting them are all validated at construction time:

    - every evidence record must be traceable to this result's
      task/version and execution, and must cite one of the result's
      stated acceptance criteria;
    - a result claiming completion (SUCCESS) must state acceptance
      criteria, must carry evidence, and every stated criterion must be
      covered by at least one evidence record.

    ``payload`` optionally carries the raw execution result (for
    example a JEV result) without making this model depend on it.
    """

    result_id: str
    task_id: str
    task_version: str
    requirement_version: str
    execution_id: str
    correlation_id: str
    status: ResultStatus | str
    acceptance_criteria: tuple[str, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    summary: str = ""
    detail: str = ""
    payload: Any = None

    def __post_init__(self) -> None:
        for field in (
            "result_id",
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
        object.__setattr__(
            self,
            "acceptance_criteria",
            _require_entries(self.acceptance_criteria, "acceptance_criteria"),
        )
        object.__setattr__(self, "evidence", _coerce_evidence(self.evidence))

        _require_text(self.summary, "summary")
        _require_text(self.detail, "detail")

        if self.claims_completion and not self.acceptance_criteria:
            raise ArtifactValidationError(
                f"result {self.result_id!r} claims completion (SUCCESS) "
                "without stated acceptance criteria"
            )

        criteria = frozenset(self.acceptance_criteria)
        for item in self.evidence:
            if (
                item.task_id != self.task_id
                or item.task_version != self.task_version
                or item.execution_id != self.execution_id
                or item.requirement_version != self.requirement_version
            ):
                raise ArtifactValidationError(
                    f"evidence {item.evidence_id!r} is not traceable to "
                    f"task {self.task_id!r} version {self.task_version!r} "
                    f"execution {self.execution_id!r} requirement "
                    f"{self.requirement_version!r}"
                )
            if item.acceptance_criterion not in criteria:
                raise ArtifactValidationError(
                    f"evidence {item.evidence_id!r} cites acceptance "
                    "criterion not stated by this result: "
                    f"{item.acceptance_criterion!r}"
                )

        if self.claims_completion:
            if not self.evidence:
                raise ArtifactValidationError(
                    f"result {self.result_id!r} claims completion "
                    "(SUCCESS) without required evidence"
                )
            covered = {
                item.acceptance_criterion for item in self.evidence
            }
            uncovered = [
                criterion
                for criterion in self.acceptance_criteria
                if criterion not in covered
            ]
            if uncovered:
                raise ArtifactValidationError(
                    f"result {self.result_id!r} claims completion "
                    "(SUCCESS) without required evidence for acceptance "
                    "criteria: "
                    + ", ".join(repr(c) for c in uncovered)
                )

    @property
    def claims_completion(self) -> bool:
        """True when this result claims completion of its criteria."""

        return self.status is ResultStatus.SUCCESS

    @property
    def covered_criteria(self) -> tuple[str, ...]:
        """The stated acceptance criteria that have evidence, in order."""

        covered = {item.acceptance_criterion for item in self.evidence}
        return tuple(c for c in self.acceptance_criteria if c in covered)

    def evidence_for(self, criterion: str) -> tuple[EvidenceRecord, ...]:
        """Return every evidence record cited for *criterion*."""

        _require_identity(criterion, "criterion")
        return tuple(
            item
            for item in self.evidence
            if item.acceptance_criterion == criterion
        )


# ------------------------------------------------------------------
# Traceability validation
# ------------------------------------------------------------------

def validate_traceability(
    result: object, task: object, execution: object
) -> None:
    """Validate the full V1.1 evidence chain for one result.

    Re-checks the chain Requirement -> Acceptance Criterion ->
    Verification Method -> Evidence -> Result against the authoritative
    TASK-002 artifacts:

    - the result must be bound to the exact task identity, task version,
      requirement version, execution identity, and correlation ID;
    - every acceptance criterion the result states — and every criterion
      its evidence cites — must be defined by the task;
    - when the task declares a verification definition, every evidence
      record must cite that declared verification method.

    Raises :class:`ArtifactValidationError` on any break in the chain.
    """

    if not isinstance(result, ExecutionResult):
        raise ArtifactValidationError(
            "result must be an ExecutionResult"
        )
    if not isinstance(task, TaskArtifact):
        raise ArtifactValidationError("task must be a TaskArtifact")
    if not isinstance(execution, ExecutionArtifact):
        raise ArtifactValidationError("execution must be an ExecutionArtifact")

    if (
        result.task_id != task.task_id
        or result.task_version != task.task_version
    ):
        raise ArtifactValidationError(
            f"result {result.result_id!r} is bound to task "
            f"{result.task_id!r} version {result.task_version!r}, not "
            f"{task.task_id!r} version {task.task_version!r}"
        )

    if result.requirement_version != task.requirement_version:
        raise ArtifactValidationError(
            f"result {result.result_id!r} claims requirement version "
            f"{result.requirement_version!r}; the task declares "
            f"{task.requirement_version!r}"
        )

    if (
        result.execution_id != execution.execution_id
        or result.correlation_id != execution.correlation_id
    ):
        raise ArtifactValidationError(
            f"result {result.result_id!r} cites execution "
            f"{result.execution_id!r}/correlation "
            f"{result.correlation_id!r}, not the declared execution "
            f"{execution.execution_id!r}/{execution.correlation_id!r}"
        )

    task_criteria = frozenset(task.acceptance_criteria)

    for criterion in result.acceptance_criteria:
        if criterion not in task_criteria:
            raise ArtifactValidationError(
                f"result {result.result_id!r} states acceptance criterion "
                f"not defined by {task.task_id!r}: {criterion!r}"
            )

    for item in result.evidence:
        if item.acceptance_criterion not in task_criteria:
            raise ArtifactValidationError(
                f"evidence {item.evidence_id!r} cites acceptance criterion "
                f"not defined by {task.task_id!r}: "
                f"{item.acceptance_criterion!r}"
            )
        if (
            task.verification is not None
            and item.verification_method != task.verification.method
        ):
            raise ArtifactValidationError(
                f"evidence {item.evidence_id!r} cites verification method "
                f"{item.verification_method!r}; {task.task_id!r} declares "
                f"{task.verification.method!r}"
            )
