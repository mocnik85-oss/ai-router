"""Interrogator AI-Assisted Project Lifecycle Protocol V1.1 — PM
interpretation layer.

Protocol V1.1 governance invariant:

    RAW EXECUTION STATE / RESULT
            |
            v
    PM INTERPRETATION
            |
            v
    AUTHORITATIVE TASK/PROJECT STATE

An :class:`~protocol.results.ExecutionResult` is raw execution state:
it reports what one execution claimed.  It must never be copied
directly into the task/project lifecycle.  This module is the explicit
interpretation boundary that:

1. receives a structured :class:`~protocol.results.ExecutionResult`;
2. inspects its :class:`~protocol.results.ResultStatus` (SUCCESS,
   PARTIAL, FAILURE, BLOCKED, NEEDS_USER — result statuses, which are
   **not** task states);
3. validates the required acceptance evidence against the
   authoritative :class:`~protocol.artifacts.TaskArtifact` before a
   SUCCESS claim may be treated as completion;
4. produces an explicit :class:`PMDecision` for the Project Manager
   to process.

The layer only *produces* a decision.  It deliberately cannot mutate
or bypass the authoritative lifecycle:

- it never writes to the (frozen) ``ProjectArtifact``/``TaskArtifact``
  inputs and never forges authoritative artifacts;
- it never maps a decision onto a V1.1 task or handoff lifecycle
  state — mapping a decision onto an authoritative transition stays
  the Project Manager's responsibility, so this layer never becomes a
  second PM;
- no decision carries a proposed task-state transition.

Decision kinds distinguish COMPLETE, REROUTE, BLOCKED, and NEEDS_USER,
plus REJECTED for contradictory or invalid result/evidence
combinations: identity, task-version, requirement-version, execution
or correlation mismatches, acceptance criteria or verification methods
not defined by the task, and SUCCESS claims that lack the acceptance
evidence the task requires.

Like :mod:`protocol.lease` and :mod:`protocol.results`, this module
depends only on the ``protocol`` package plus the standard library —
never on ``router``/JEV, a provider, or any paid model/API — so the
interpretation layer is testable independently of execution/provider
code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    _coerce_state,
    _require_entries,
    _require_identity,
)
from protocol.results import (
    EvidenceRecord,
    ExecutionResult,
    ResultStatus,
    _coerce_evidence,
    validate_traceability,
)


class PMDecisionKind(StrEnum):
    """The explicit PM decisions the interpretation layer can produce.

    These are *decisions for the Project Manager*, not V1.1 task
    lifecycle states.  Turning a decision into an authoritative
    task-state transition remains the PM's responsibility; nothing in
    this module performs that step.  Identically named decision kinds
    (``BLOCKED``, ``NEEDS_USER``) are still distinct from the
    :class:`~protocol.artifacts.TaskState` members of the same name.
    """

    COMPLETE = "COMPLETE"
    REROUTE = "REROUTE"
    BLOCKED = "BLOCKED"
    NEEDS_USER = "NEEDS_USER"
    REJECTED = "REJECTED"


#: Decision produced for each result status that does not claim
#: completion.  SUCCESS is handled separately because it additionally
#: requires acceptance evidence covering every criterion the task
#: declares.
_NON_COMPLETION_DECISIONS: dict[ResultStatus, PMDecisionKind] = {
    ResultStatus.PARTIAL: PMDecisionKind.REROUTE,
    ResultStatus.FAILURE: PMDecisionKind.REROUTE,
    ResultStatus.BLOCKED: PMDecisionKind.BLOCKED,
    ResultStatus.NEEDS_USER: PMDecisionKind.NEEDS_USER,
}

#: Stable explanation recorded with each non-completion decision.  The
#: source :class:`ResultStatus` is also carried on the decision, so
#: PARTIAL and FAILURE stay distinguishable even though both reroute.
_NON_COMPLETION_REASONS: dict[ResultStatus, str] = {
    ResultStatus.PARTIAL: (
        "PARTIAL result: the execution did not verify every acceptance "
        "criterion; the task is not complete and the remaining work must "
        "be rerouted."
    ),
    ResultStatus.FAILURE: (
        "FAILURE result: the execution failed; the task is not complete "
        "and the result must be rerouted."
    ),
    ResultStatus.BLOCKED: (
        "BLOCKED result: the execution reported a blocking condition; the "
        "task is not complete and the block must be resolved."
    ),
    ResultStatus.NEEDS_USER: (
        "NEEDS_USER result: the execution requires user input; the task "
        "is not complete and the user must decide."
    ),
}


# ------------------------------------------------------------------
# Decision artifact
# ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PMDecision:
    """One explicit PM decision produced from one execution result.

    The decision is a record for the Project Manager — it is frozen,
    proposes no task-state transition, and therefore cannot by itself
    change authoritative project/task state.

    It retains full traceability to:

    - project identity: ``project`` (when supplied)
    - task identity: ``task_id`` / ``task_version`` /
      ``observed_task_state`` (read-only snapshot at interpretation
      time, not a transition)
    - requirement version: ``requirement_version``
    - execution identity: ``execution_id`` / ``correlation_id``
    - acceptance criteria: ``acceptance_criteria`` (the authoritative
      task's criteria) and ``covered_criteria``
    - evidence: ``evidence`` and ``result_id`` (the interpreted
      :class:`~protocol.results.ExecutionResult`)

    Task and execution identity fields are anchored to the
    *authoritative* artifacts the decision is rendered against; when a
    rejected result claims different identities, the contradicting
    claims are recorded in ``reasons`` and remain reachable through
    ``result_id``.

    Required identity fields and the decision/status consistency rules
    are validated at construction time, so a decision record can never
    claim ``COMPLETE`` without acceptance evidence covering every
    acceptance criterion, and never pair ``COMPLETE`` with a
    non-SUCCESS result status (or vice versa).
    """

    decision_id: str
    kind: PMDecisionKind | str
    project: ProjectArtifact | None
    task_id: str
    task_version: str
    observed_task_state: TaskState | str
    requirement_version: str
    result_id: str
    result_status: ResultStatus | str
    execution_id: str
    correlation_id: str
    acceptance_criteria: tuple[str, ...] = ()
    covered_criteria: tuple[str, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "decision_id",
            "task_id",
            "task_version",
            "requirement_version",
            "result_id",
            "execution_id",
            "correlation_id",
        ):
            _require_identity(getattr(self, field), field)

        if self.project is not None and not isinstance(
            self.project, ProjectArtifact
        ):
            raise ArtifactValidationError(
                "project must be a ProjectArtifact or None"
            )

        object.__setattr__(
            self, "kind", _coerce_state(self.kind, PMDecisionKind, "kind")
        )
        object.__setattr__(
            self,
            "result_status",
            _coerce_state(self.result_status, ResultStatus, "result_status"),
        )
        object.__setattr__(
            self,
            "observed_task_state",
            _coerce_state(self.observed_task_state, TaskState,
                          "observed_task_state"),
        )
        object.__setattr__(
            self,
            "acceptance_criteria",
            _require_entries(self.acceptance_criteria, "acceptance_criteria"),
        )
        object.__setattr__(
            self,
            "covered_criteria",
            _require_entries(self.covered_criteria, "covered_criteria"),
        )
        object.__setattr__(self, "evidence", _coerce_evidence(self.evidence))
        object.__setattr__(
            self, "reasons", _require_entries(self.reasons, "reasons")
        )

        uncovered = [
            criterion
            for criterion in self.covered_criteria
            if criterion not in self.acceptance_criteria
        ]
        if uncovered:
            raise ArtifactValidationError(
                "covered_criteria cites acceptance criteria outside "
                "acceptance_criteria: "
                + ", ".join(repr(c) for c in uncovered)
            )

        # Covered criteria must always be backed by the cited evidence,
        # so a decision can never claim coverage it cannot show.
        evidence_criteria = {
            item.acceptance_criterion for item in self.evidence
        }
        recomputed = tuple(
            criterion
            for criterion in self.acceptance_criteria
            if criterion in evidence_criteria
        )
        if recomputed != self.covered_criteria:
            raise ArtifactValidationError(
                "covered_criteria does not match the cited evidence "
                f"records: expected {recomputed!r}, got "
                f"{self.covered_criteria!r}"
            )

        if self.kind is PMDecisionKind.COMPLETE:
            if not self.acceptance_criteria:
                raise ArtifactValidationError(
                    "a COMPLETE decision requires acceptance criteria "
                    "declared by the task"
                )
            if tuple(self.covered_criteria) != tuple(
                self.acceptance_criteria
            ):
                raise ArtifactValidationError(
                    "a COMPLETE decision requires acceptance evidence "
                    "covering every acceptance criterion"
                )
            if self.result_status is not ResultStatus.SUCCESS:
                raise ArtifactValidationError(
                    "a COMPLETE decision requires a SUCCESS result status"
                )
        elif (
            self.result_status is ResultStatus.SUCCESS
            and self.kind is not PMDecisionKind.REJECTED
        ):
            raise ArtifactValidationError(
                f"a {self.result_status} result status cannot be recorded "
                f"as a {self.kind} decision"
            )


# ------------------------------------------------------------------
# Interpreter
# ------------------------------------------------------------------

class PMInterpreter:
    """The explicit boundary between raw execution results and
    authoritative task/project state.

    ``interpret`` is a pure function of its inputs: it reads the
    authoritative artifacts and returns a frozen :class:`PMDecision`.
    It never writes to them, never returns a
    :class:`~protocol.artifacts.TaskArtifact`, and never suggests a
    lifecycle transition — the Project Manager processes the returned
    decision and remains the only authority that changes task state.

    Interpretation rules:

    ================== ============================== =====================
    Result status      Condition                      Decision
    ================== ============================== =====================
    SUCCESS            evidence covers every          COMPLETE
                       task acceptance criterion
    SUCCESS            acceptance evidence missing    REJECTED
    PARTIAL            (always)                       REROUTE
    FAILURE            (always)                       REROUTE
    BLOCKED            (always)                       BLOCKED
    NEEDS_USER         (always)                       NEEDS_USER
    any                identity/version/criterion/    REJECTED
                       verification-method mismatch
    ================== ============================== =====================

    Wrong argument *types* raise
    :class:`~protocol.artifacts.ArtifactValidationError` (API misuse);
    contradictory or invalid result/evidence *content* is never raised
    away — it is safely routed to the PM as a ``REJECTED`` decision
    with the reason recorded.
    """

    def interpret(
        self,
        result: ExecutionResult,
        task: TaskArtifact,
        execution: ExecutionArtifact,
        *,
        project: ProjectArtifact | None = None,
    ) -> PMDecision:
        """Interpret *result* against the authoritative *task* and
        *execution* artifacts and return the explicit PM decision.

        *result* is raw execution state.  The returned
        :class:`PMDecision` is the only output; the authoritative
        artifacts are only read, never modified.
        """

        if not isinstance(result, ExecutionResult):
            raise ArtifactValidationError(
                "result must be an ExecutionResult"
            )
        if not isinstance(task, TaskArtifact):
            raise ArtifactValidationError("task must be a TaskArtifact")
        if not isinstance(execution, ExecutionArtifact):
            raise ArtifactValidationError(
                "execution must be an ExecutionArtifact"
            )
        if project is not None and not isinstance(project, ProjectArtifact):
            raise ArtifactValidationError(
                "project must be a ProjectArtifact or None"
            )

        reasons: list[str] = []
        required = task.acceptance_criteria
        evidence_criteria = {
            item.acceptance_criterion for item in result.evidence
        }
        covered = tuple(
            criterion for criterion in required
            if criterion in evidence_criteria
        )

        # Full chain check: Requirement -> Acceptance Criterion ->
        # Verification Method -> Evidence -> Result against the
        # authoritative artifacts.  Any break is routed to the PM as a
        # rejection instead of being raised away.
        kind: PMDecisionKind | None = None
        try:
            validate_traceability(result, task, execution)
        except ArtifactValidationError as exc:
            kind = PMDecisionKind.REJECTED
            reasons.append(f"rejected: {exc}")

        if kind is None:
            if result.status is ResultStatus.SUCCESS:
                kind = self._completion_decision(
                    task, required, covered, reasons
                )
            else:
                kind = _NON_COMPLETION_DECISIONS[result.status]
                reasons.append(_NON_COMPLETION_REASONS[result.status])

        return PMDecision(
            decision_id=f"DECISION-{result.result_id}",
            kind=kind,
            project=project,
            task_id=task.task_id,
            task_version=task.task_version,
            observed_task_state=task.state,
            requirement_version=task.requirement_version,
            result_id=result.result_id,
            result_status=result.status,
            execution_id=execution.execution_id,
            correlation_id=execution.correlation_id,
            acceptance_criteria=required,
            covered_criteria=covered,
            evidence=result.evidence,
            reasons=tuple(reasons),
        )

    @staticmethod
    def _completion_decision(
        task: TaskArtifact,
        required: tuple[str, ...],
        covered: tuple[str, ...],
        reasons: list[str],
    ) -> PMDecisionKind:
        """Decide for a SUCCESS claim, gated on acceptance evidence.

        Completion is only proposed when the evidence covers every
        acceptance criterion the authoritative task declares — not
        merely the criteria the result chose to state.
        """

        if not required:
            reasons.append(
                "rejected: a SUCCESS result cannot be accepted because "
                f"{task.task_id!r} declares no acceptance criteria"
            )
            return PMDecisionKind.REJECTED

        uncovered = tuple(
            criterion for criterion in required if criterion not in covered
        )
        if uncovered:
            reasons.append(
                "rejected: SUCCESS lacks the acceptance evidence required "
                "for acceptance criteria: "
                + ", ".join(repr(c) for c in uncovered)
            )
            return PMDecisionKind.REJECTED

        reasons.append(
            "SUCCESS with acceptance evidence covering every acceptance "
            f"criterion declared by {task.task_id!r}"
        )
        return PMDecisionKind.COMPLETE
