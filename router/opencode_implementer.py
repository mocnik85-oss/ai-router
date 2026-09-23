"""Router V1.1 — the OpenCode Implementer adapter (MVP-005).

This module is the first concrete backend adapter for the
backend-independent Implementer contract established by
``protocol.implementer``.  The intended relationship is:

    PM / orchestration
            |
            v
    generic Implementer contract (protocol.implementer)
            |
            v
    OpenCode Implementer adapter (this module)
            |
            v
    existing OpenCode/JEV execution mechanism
    (router.jev -> providers.opencode)

The adapter performs **translation only**, in both directions:

Input — :meth:`OpenCodeImplementer.build_jev_task` renders an
:class:`~protocol.implementer.ImplementationTask` into the execution
request the existing dispatch core already understands
(:class:`router.jev.JEVTask`): the instruction, acceptance criteria,
verification definition, dependencies, and the project/task/execution
traceability identities are rendered into the prompt, while the
configured workspace and the governing read-only execution policy
travel as JEV configuration — never as fields of the generic contract.

Output — :meth:`OpenCodeImplementer.build_report` maps the raw
:class:`router.jev.JEVResult` back onto a generic
:class:`~protocol.implementer.ImplementationReport` bound to exactly
the handed-off task and execution identities, carrying a structured
:class:`~protocol.results.ExecutionResult` (status, acceptance
criteria, evidence, narrative, backend payload).  Every report has
passed :func:`protocol.implementer.validate_report` against the task
it answers before it is returned.

Status mapping is mechanical and derived only from the observable
shape of the raw result — never from interpreting narrative content:

============================================ ===================
Raw JEV result                               Report status
============================================ ===================
``error`` set (dispatch produced no         BLOCKED
outcome: no free model, provider error,
timeout); requires ``success is False``
and ``exit_code == -1``
``exit_code == 0`` and ``success is True``   SUCCESS
``exit_code != 0`` and ``success is False``  FAILURE
============================================ ===================

Anything else fails closed with
:class:`OpenCodeImplementerError`: a foreign (non-``JEVResult``)
object, a success flag contradicting the exit code, a dispatch error
that also claims a process exit, a blank dispatch error, a
non-integer exit code, or a result that does not echo the dispatched
prompt.  ``PARTIAL`` and ``NEEDS_USER`` are deliberately *not*
producible: the OpenCode/JEV execution boundary carries no signal for
them, and this adapter never guesses an outcome from free text.
Deciding what any status means for the task stays with
``protocol.interpretation`` and the Project Manager.

What deliberately stays outside this adapter (unchanged V1.1
authority boundaries):

- **PM lifecycle authority.** The adapter never checks approval,
  never schedules, never resolves dependencies or batches, never
  transitions task/handoff state, and never interprets a result into
  a decision — it references no PM decision model at all.  Consuming
  the returned report remains the PM side's job.
- **Policy, workspace, network, credential, and safety ceilings.**
  The workspace and the available model catalogue are caller-supplied
  configuration (not decisions this adapter makes), the governing
  execution policy is JEV's own read-only default re-enforced at the
  provider boundary, and no Git, credential, network, filesystem-write,
  recovery, Tester, or Reviewer action exists here.
- **The generic contract.**  ``protocol.implementer`` is untouched and
  stays backend-independent: every OpenCode/JEV-specific name lives in
  this module only.

Reuse and testability: the existing JEV execution abstraction is used
as-is (``router.jev.JEV`` with its injectable
:class:`~providers.opencode.OpenCodeClient` and its per-dispatch
``available_models`` catalogue), so no parallel execution system is
introduced and the whole adapter is exercised offline with injected
fakes — the normal test suite needs no live OpenCode process, no
model-catalogue fetch, and no network call.  A backend that *raises*
propagates the exception: the adapter never fabricates a report for an
execution that produced no result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol.artifacts import ArtifactValidationError
from protocol.implementer import (
    ImplementationReport,
    ImplementationTask,
    validate_report,
)
from protocol.results import EvidenceRecord, ExecutionResult, ResultStatus
from router.jev import JEV, JEVResult, JEVTask


class OpenCodeImplementerError(RuntimeError):
    """Raised when an OpenCode execution result cannot be mapped safely.

    The adapter fails closed with this error instead of returning a
    report it cannot construct honestly: a foreign raw result, a
    success flag contradicting the exit code, a dispatch error that
    also claims a process exit, a blank dispatch error, a
    non-integer exit code, or a result that does not answer the
    dispatched request.  It is an execution-translation error, never a
    governance decision and never a lifecycle transition.
    """


class OpenCodeImplementer:
    """The OpenCode backend adapter for the generic Implementer contract.

    Satisfies :class:`protocol.implementer.Implementer` structurally:
    :meth:`execute` accepts one :class:`ImplementationTask` and
    returns one :class:`ImplementationReport`, translating through the
    existing JEV execution mechanism:

    1. render the generic task into a :class:`router.jev.JEVTask`
       (:meth:`build_jev_task`) — a backend execution request, not a
       governance record;
    2. dispatch it through the injected :class:`router.jev.JEV`
       (:meth:`execute`), which returns the raw execution result;
    3. map that raw result onto the generic report
       (:meth:`build_report`), binding every identity back to the
       handed-off task and execution and validating the result with
       the contract's own fail-closed check.

    Construction takes only backend execution configuration — the
    workspace to run in, an optional injectable JEV (whose OpenCode
    client is itself injectable), and the optional model catalogue
    passed through per dispatch.  The adapter holds no approval, no
    schedule, no policy decision, and no authority of any kind.
    """

    #: Name reported by every report this adapter produces.
    IMPLEMENTER_NAME = "opencode"

    #: Verification method cited in adapter-built evidence when the
    #: handed-off task declares no verification definition.  A task's
    #: own declared method always wins (the evidence chain requires it).
    DEFAULT_VERIFICATION_METHOD = "OpenCode execution report"

    #: Backend identity recorded in the result payload metadata.
    BACKEND_NAME = "opencode"

    def __init__(
        self,
        *,
        workdir: str | Path,
        jev: JEV | None = None,
        available_models: list[Any] | None = None,
    ) -> None:
        if not isinstance(workdir, (str, Path)) or not str(workdir).strip():
            raise OpenCodeImplementerError(
                "workdir must be a non-empty path; the adapter executes "
                "exactly the workspace its caller configured"
            )
        self._workdir = str(workdir)
        self._jev = jev if jev is not None else JEV()
        self._available_models = available_models

    @property
    def workdir(self) -> str:
        """The caller-configured workspace this adapter dispatches into."""

        return self._workdir

    # ------------------------------------------------------------------
    # Input mapping: generic task -> OpenCode/JEV execution request
    # ------------------------------------------------------------------

    def build_jev_task(self, task: ImplementationTask) -> JEVTask:
        """Render *task* into the existing OpenCode/JEV execution request.

        The prompt carries the requested work plus every traceability
        identity (project, task/version, requirement version,
        execution/correlation/idempotency), the acceptance criteria,
        the dependencies, and the verification definition, so the
        backend runs against exactly the handed-off work.  The
        workspace and policy are JEV configuration supplied here by
        the adapter's caller and JEV's read-only default — the generic
        contract itself carries no workspace or policy field, and this
        method adds none.

        An input that is not an :class:`ImplementationTask` is refused
        before anything is dispatched.
        """

        if not isinstance(task, ImplementationTask):
            raise ArtifactValidationError(
                "task must be an ImplementationTask"
            )
        return JEVTask(
            prompt=self._render_prompt(task),
            workdir=self._workdir,
        )

    @staticmethod
    def _render_prompt(task: ImplementationTask) -> str:
        """Render one generic task as the OpenCode prompt text."""

        artifact = task.task
        execution = task.execution
        dependencies = ", ".join(artifact.dependencies) or "(none)"
        lines = [
            "Implement this V1.1 implementation task.",
            "",
            f"Project: {task.project.project_id} "
            f"({task.project.project_version})",
            f"Task: {artifact.task_id} ({artifact.task_version})",
            f"Requirement version: {artifact.requirement_version}",
            f"Execution: {execution.execution_id}",
            f"Correlation: {execution.correlation_id}",
            f"Idempotency key: {execution.idempotency_key}",
            f"Dependencies: {dependencies}",
            "",
            "Instruction:",
            task.instruction,
            "",
            "Acceptance criteria:",
        ]
        lines.extend(f"- {criterion}" for criterion in artifact.acceptance_criteria)
        if artifact.verification is not None:
            lines.append("")
            lines.append(f"Verification method: {artifact.verification.method}")
            if artifact.verification.description:
                lines.append(
                    "Verification description: "
                    f"{artifact.verification.description}"
                )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Execution: the existing OpenCode/JEV mechanism
    # ------------------------------------------------------------------

    def execute(self, task: ImplementationTask) -> ImplementationReport:
        """Perform *task*'s requested work and report its outcome.

        The generic contract's ``execute``: validate/render the input,
        dispatch through the existing JEV execution mechanism, and map
        the raw result onto a validated generic report.  Execution
        errors raised by the backend propagate unchanged — no report
        is fabricated for an execution that produced no result — and
        no approval, scheduling, interpretation, or lifecycle
        decision happens at any point.
        """

        request = self.build_jev_task(task)
        raw = self._jev.run(request, available_models=self._available_models)
        return self.build_report(task, raw)

    # ------------------------------------------------------------------
    # Output mapping: raw OpenCode/JEV result -> generic report
    # ------------------------------------------------------------------

    def build_report(
        self, task: ImplementationTask, result: JEVResult
    ) -> ImplementationReport:
        """Map one raw OpenCode/JEV execution result onto the generic report.

        Fails closed (:class:`OpenCodeImplementerError`) when *result*
        is not a :class:`router.jev.JEVResult`, does not echo the
        prompt this adapter dispatched for *task*, contradicts itself
        about success/exit code, or is otherwise ambiguous.  On the
        happy path the report is bound to exactly the handed-off
        identities and re-checked with
        :func:`protocol.implementer.validate_report` before being
        returned, so an unmappable or untraceable outcome never
        escapes as a report.
        """

        if not isinstance(task, ImplementationTask):
            raise ArtifactValidationError(
                "task must be ImplementationTask"
            )
        if not isinstance(result, JEVResult):
            raise OpenCodeImplementerError(
                "expected a JEVResult from the OpenCode execution "
                f"boundary; got {type(result).__name__!r}"
            )
        if result.prompt != self._render_prompt(task):
            raise OpenCodeImplementerError(
                "OpenCode execution result does not echo the dispatched "
                "prompt; refusing a result that does not answer this "
                "task's request"
            )

        status, narrative = self._map_status(result)
        execution = task.execution
        artifact = task.task

        structured = ExecutionResult(
            result_id=f"RESULT-{execution.execution_id}",
            task_id=artifact.task_id,
            task_version=artifact.task_version,
            requirement_version=artifact.requirement_version,
            execution_id=execution.execution_id,
            correlation_id=execution.correlation_id,
            status=status,
            acceptance_criteria=artifact.acceptance_criteria,
            evidence=(
                self._evidence(task, result)
                if status is ResultStatus.SUCCESS
                else ()
            ),
            summary=self._summary(execution.execution_id, status, result),
            detail=narrative,
            payload=self._payload(result),
        )

        report = ImplementationReport(
            task_id=artifact.task_id,
            task_version=artifact.task_version,
            requirement_version=artifact.requirement_version,
            execution_id=execution.execution_id,
            correlation_id=execution.correlation_id,
            status=status,
            result=structured,
            detail=narrative,
            implementer=self.IMPLEMENTER_NAME,
        )
        # The contract's own fail-closed consumption check, run before
        # the report leaves the adapter: binding and evidence chain
        # must hold against exactly the handed-off task and execution.
        validate_report(report, task)
        return report

    @staticmethod
    def _map_status(result: JEVResult) -> tuple[ResultStatus, str]:
        """Derive the result status from the raw result's shape only.

        Mechanical, closed, and content-blind: narrative text is never
        parsed for an outcome, and anything that does not fit the
        table exactly raises instead of being guessed at.
        """

        if result.error is not None:
            # A dispatch-level failure: JEV reports it as success False
            # with its -1 sentinel exit code.  Anything else mixes two
            # mutually exclusive execution outcomes (a dispatch error
            # *and* a concrete process exit) and is ambiguous.
            if result.success is not False:
                raise OpenCodeImplementerError(
                    "ambiguous OpenCode execution result: a dispatch "
                    "error is reported together with success="
                    f"{result.success!r}"
                )
            if not isinstance(result.exit_code, int) or isinstance(
                result.exit_code, bool
            ):
                raise OpenCodeImplementerError(
                    "ambiguous OpenCode execution result: exit code "
                    f"{result.exit_code!r} is not an integer"
                )
            if result.exit_code != -1:
                raise OpenCodeImplementerError(
                    "ambiguous OpenCode execution result: a dispatch "
                    f"error claims process exit code {result.exit_code}; "
                    "the backend never produced a process outcome"
                )
            if not isinstance(result.error, str) or not result.error.strip():
                raise OpenCodeImplementerError(
                    "ambiguous OpenCode execution result: the dispatch "
                    "error carries no message"
                )
            return ResultStatus.BLOCKED, result.error

        if not isinstance(result.exit_code, int) or isinstance(
            result.exit_code, bool
        ):
            raise OpenCodeImplementerError(
                "ambiguous OpenCode execution result: exit code "
                f"{result.exit_code!r} is not an integer"
            )
        succeeded = result.exit_code == 0
        if result.success is not succeeded:
            raise OpenCodeImplementerError(
                "ambiguous OpenCode execution result: success="
                f"{result.success} contradicts exit code "
                f"{result.exit_code}"
            )
        if succeeded:
            return ResultStatus.SUCCESS, result.text
        narrative = result.text.strip() or (
            f"OpenCode exited with code {result.exit_code}."
        )
        return ResultStatus.FAILURE, narrative

    @staticmethod
    def _summary(
        execution_id: str, status: ResultStatus, result: JEVResult
    ) -> str:
        """One-line backend outcome summary for the structured result."""

        if status is ResultStatus.SUCCESS:
            return (
                f"OpenCode execution {execution_id} finished with "
                "exit code 0."
            )
        if status is ResultStatus.BLOCKED:
            return (
                f"OpenCode execution {execution_id} did not run: "
                f"{result.error}"
            )
        return (
            f"OpenCode execution {execution_id} failed with exit code "
            f"{result.exit_code}."
        )

    @staticmethod
    def _payload(result: JEVResult) -> dict:
        """JSON-representable backend execution metadata for the payload.

        Keeps backend-specific execution metadata (model, exit code,
        dispatch error, dispatched prompt) inside the generic
        ``payload`` slot instead of leaking it into the contract's
        identity/status fields — and stays plain JSON so the report
        round-trips through ``to_json``.
        """

        return {
            "backend": OpenCodeImplementer.BACKEND_NAME,
            "model": result.model,
            "exit_code": result.exit_code,
            "success": result.success,
            "dispatch_error": result.error,
            "prompt": result.prompt,
        }

    @classmethod
    def _evidence(
        cls, task: ImplementationTask, result: JEVResult
    ) -> tuple[EvidenceRecord, ...]:
        """Build the backend's per-criterion acceptance claim.

        A ``SUCCESS`` result must state acceptance evidence for every
        criterion it claims (the contract refuses a bare completion
        claim).  The adapter records *the backend's own reported
        claim* — "OpenCode reported exit code 0" — against each of the
        handed-off task's acceptance criteria, citing the task's
        declared verification method so the evidence chain holds.
        This is the implementer's claim, not a verification verdict:
        verifying it stays with the Tester/Reviewer stages and the
        Project Manager.
        """

        artifact = task.task
        execution = task.execution
        method = (
            artifact.verification.method
            if artifact.verification is not None
            else cls.DEFAULT_VERIFICATION_METHOD
        )
        reference = "opencode exit_code=0"
        if result.model:
            reference = f"{reference} model={result.model}"
        return tuple(
            EvidenceRecord(
                evidence_id=(
                    f"EVID-{execution.execution_id}-{index:03d}"
                ),
                task_id=artifact.task_id,
                task_version=artifact.task_version,
                execution_id=execution.execution_id,
                requirement_version=artifact.requirement_version,
                acceptance_criterion=criterion,
                verification_method=method,
                summary=(
                    f"OpenCode execution {execution.execution_id} "
                    "reported success at exit code 0."
                ),
                reference=reference,
            )
            for index, criterion in enumerate(
                artifact.acceptance_criteria, start=1
            )
        )
