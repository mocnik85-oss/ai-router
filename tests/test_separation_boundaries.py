"""TASK-007 focused tests — separation of governance, orchestration,
and specialist/provider execution.

Covers the V1.1 control boundary:

    RAW EXECUTION STATE / RESULT
            -> PM INTERPRETATION
            -> AUTHORITATIVE TASK / PROJECT STATE

Evidence produced here (offline only — no network, model, or real
provider calls):

- provider execution returns execution/result information, never
  authoritative lifecycle state, and never sees governance artifacts;
- the mechanical orchestrator dispatches, enforces the exclusive
  execution lease, and transports raw results, but cannot import or
  produce PM decisions and cannot mutate authoritative task/project
  state;
- PM interpretation remains the single place where results are
  converted into decisions, and even it never mutates authoritative
  state — transitions stay with the Project Manager;
- no implementation layer (router/, providers/, app/, or governance
  outside the interpretation module) references the decision model at
  all — a source-level boundary, not a convention;
- existing JEV behavior, read-only policy enforcement, and the
  lease/result/interpretation models remain intact: this file is run
  as the focused set together with the unchanged TASK-002..TASK-006
  suites.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import providers.opencode as opencode_module
import router.orchestration as orchestration_module
from providers.opencode import OpenCodeClient, OpenCodeResult, OpenCodeEvent
from providers.openrouter import OpenRouterModel
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
from protocol.interpretation import PMDecision, PMDecisionKind, PMInterpreter
from protocol.lease import (
    DuplicateExecutionError,
    DuplicateLeaseError,
    ExecutionLeaseManager,
    ExecutionState,
    LeaseError,
    LeaseState,
    ReconciliationRequiredError,
)
from protocol.results import EvidenceRecord, ExecutionResult, ResultStatus
from router.jev import JEV, JEVResult, JEVTask, PolicyViolationError
from router.orchestration import DispatchRecord, OrchestrationError, Orchestrator
from router.policy import ExecutionPolicy


REPO_ROOT = Path(__file__).resolve().parent.parent

TASK_ID = "TASK-007"
TASK_VERSION = "v1"
REQUIREMENT_VERSION = "v1.1"
EXECUTION_ID = "EXEC-TASK-007-001"
CORRELATION_ID = "CORR-TASK-007-001"
RESULT_ID = "RESULT-TASK-007-001"
LEASE_ID = "LEASE-TASK-007-001"
PROJECT_ID = "ai-router"
PROJECT_VERSION = "v1.1-migration"

CRITERION_1 = (
    "Orchestration is mechanical and transports raw execution state."
)
CRITERION_2 = "PM interpretation converts a result into a decision."
CRITERIA = (CRITERION_1, CRITERION_2)
METHOD = "Focused TASK-007 separation tests"

#: Every closed V1.1 token set: task states, handoff states, PM
#: decision kinds, and result statuses.  None of these may appear as a
#: string in the mechanical orchestration layer.
BANNED_STATE_TOKENS = frozenset(
    {
        # TaskState
        "PENDING", "READY", "IMPLEMENTING", "VERIFYING", "AUDITING",
        "COMPLETED", "BLOCKED", "NEEDS_USER", "CANCELLED", "OBSOLETE",
        # HandoffState
        "DISPATCHED", "IN_PROGRESS", "RESULT_READY", "PROCESSED",
        "SUPERSEDED",
        # PMDecisionKind
        "COMPLETE", "REROUTE", "REJECTED",
        # ResultStatus
        "SUCCESS", "PARTIAL", "FAILURE",
    }
)

#: Names whose presence in an implementation layer would mean that
#: layer holds PM decision or authoritative lifecycle authority.
BANNED_AUTHORITY_NAMES = frozenset(
    {
        "PMDecision",
        "PMDecisionKind",
        "PMInterpreter",
        "TaskState",
        "HandoffState",
        "ResultStatus",
        "EvidenceRecord",
        "VerificationDefinition",
    }
)


# ------------------------------------------------------------------
# Builders
# ------------------------------------------------------------------

def _model(model_id: str = "opencode/task007-free") -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=100_000,
        prompt_price="0",
        completion_price="0",
        supports_tools=True,
        supports_vision=False,
        raw={},
    )


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


def _execution(**overrides) -> ExecutionArtifact:
    base = dict(
        execution_id=EXECUTION_ID,
        correlation_id=CORRELATION_ID,
        idempotency_key="TASK-007-v1-exec-001",
    )
    base.update(overrides)
    return ExecutionArtifact(**base)


def _project() -> ProjectArtifact:
    return ProjectArtifact(
        project_id=PROJECT_ID, project_version=PROJECT_VERSION
    )


def _evidence(
    evidence_id: str, criterion: str, *, execution_id: str = EXECUTION_ID
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        execution_id=execution_id,
        requirement_version=REQUIREMENT_VERSION,
        acceptance_criterion=criterion,
        verification_method=METHOD,
        summary="focused separation test run passed",
    )


def _specialist_result(
    raw_result: JEVResult, *, execution_id: str = EXECUTION_ID
) -> ExecutionResult:
    """The specialist/verifier packages a raw execution result into a
    structured result with evidence — the V1.1 specialist role:
    returns structured results/evidence, decided by nobody below the
    PM interpretation layer."""

    if raw_result.success:
        status = ResultStatus.SUCCESS
        evidence = (
            _evidence("EVID-TASK-007-001", CRITERION_1,
                      execution_id=execution_id),
            _evidence("EVID-TASK-007-002", CRITERION_2,
                      execution_id=execution_id),
        )
    else:
        status = ResultStatus.FAILURE
        evidence = ()

    return ExecutionResult(
        result_id=RESULT_ID,
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        execution_id=execution_id,
        correlation_id=CORRELATION_ID,
        status=status,
        acceptance_criteria=CRITERIA,
        evidence=evidence,
        summary="specialist report for one dispatch",
        detail=raw_result.error or raw_result.text,
        payload=raw_result,
    )


def _oc_result(*, exit_code: int = 0, text: str = "ok") -> OpenCodeResult:
    events: list[OpenCodeEvent] = []
    if text:
        events.append(OpenCodeEvent(type="text",
                                    raw={"part": {"text": text}}))
    return OpenCodeResult(exit_code=exit_code, events=events,
                          stdout=text, stderr="")


class _FakeOpenCodeClient(OpenCodeClient):
    """Canned-result execution backend; records every execution request."""

    def __init__(self, result: OpenCodeResult | None = None) -> None:
        super().__init__()
        self._result = result or _oc_result()
        self.requests: list[dict] = []

    def run(self, prompt, workdir, *, model=None, policy=None):
        self.requests.append(
            {"prompt": prompt, "model": model, "policy": policy}
        )
        return self._result

    def can_route(self, model_id: str) -> bool:
        return True


def _orchestrator(
    *,
    oc_result: OpenCodeResult | None = None,
    client: OpenCodeClient | None = None,
    lease_manager: ExecutionLeaseManager | None = None,
):
    """An orchestrator over a real JEV dispatch core with a canned
    execution backend (the default controller selects the free model)."""

    backend = client if client is not None else _FakeOpenCodeClient(oc_result)
    orchestrator = Orchestrator(
        jev=JEV(client=backend),
        lease_manager=lease_manager or ExecutionLeaseManager(),
    )
    return orchestrator, backend


def _dispatch(
    orchestrator: Orchestrator,
    task: TaskArtifact,
    execution: ExecutionArtifact,
    *,
    workdir: str = "/tmp",
    lease_id: str = LEASE_ID,
    models: list | None = None,
    policy: ExecutionPolicy | None = None,
    prompt: str = "inspect the repository",
) -> DispatchRecord:
    return orchestrator.dispatch(
        task=task,
        execution=execution,
        jev_task=JEVTask(
            prompt=prompt,
            workdir=workdir,
            **({} if policy is None else {"policy": policy}),
        ),
        lease_id=lease_id,
        ttl=60.0,
        available_models=[_model()] if models is None else models,
    )


# ------------------------------------------------------------------
# Source-level responsibility boundaries
# ------------------------------------------------------------------

def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _module_tree(module) -> ast.Module:
    path = Path(inspect.getsourcefile(module) or "")
    return _tree(path)


def _imported_modules(tree: ast.Module) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    return imported


def _imported_roots(tree: ast.Module) -> set[str]:
    return {name.split(".")[0] for name in _imported_modules(tree)}


def _referenced_names(tree: ast.Module) -> set[str]:
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    return referenced


class TestSourceLevelResponsibilityBoundaries:
    """The three layers are separated by import direction, not by
    convention: specialist/provider and dispatch code physically
    cannot reach governance state, and orchestration physically cannot
    reach the PM interpretation layer."""

    def test_provider_modules_never_import_governance(self) -> None:
        for path in sorted((REPO_ROOT / "providers").glob("*.py")):
            roots = _imported_roots(_tree(path))
            assert "protocol" not in roots, (
                f"{path.name} imports governance: {sorted(roots)}"
            )

    def test_dispatch_core_modules_never_import_governance(self) -> None:
        for name in (
            "jev.py", "policy.py", "free_controller.py",
            "model_resolver.py",
        ):
            roots = _imported_roots(_tree(REPO_ROOT / "router" / name))
            assert "protocol" not in roots, (
                f"router/{name} imports governance: {sorted(roots)}"
            )

    def test_governance_modules_never_import_execution_layers(self) -> None:
        for path in sorted((REPO_ROOT / "protocol").glob("*.py")):
            roots = _imported_roots(_tree(path))
            assert not roots & {"router", "providers"}, (
                f"{path.name} depends on execution code: {sorted(roots)}"
            )

    def test_orchestrator_transports_governance_but_never_interprets(
        self,
    ) -> None:
        imported = _imported_modules(_module_tree(orchestration_module))
        roots = {name.split(".")[0] for name in imported}
        # It may transport governance artifacts, leases, and results…
        assert roots <= {
            "__future__", "dataclasses", "typing", "router", "protocol"
        }
        assert "protocol.artifacts" in imported   # authorized context
        assert "protocol.lease" in imported       # lease bookkeeping
        assert "router.jev" in imported           # dispatch core
        # …but the PM interpretation layer is reachable by nobody else:
        assert "protocol.interpretation" not in imported

    def test_orchestrator_never_references_decision_or_lifecycle_authority(
        self,
    ) -> None:
        referenced = _referenced_names(_module_tree(orchestration_module))
        offenders = referenced & BANNED_AUTHORITY_NAMES
        assert offenders == set(), (
            f"orchestration references authority names: {sorted(offenders)}"
        )

    def test_orchestrator_contains_no_closed_state_tokens(self) -> None:
        tree = _module_tree(orchestration_module)
        strings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        found = strings & BANNED_STATE_TOKENS
        assert found == set(), (
            f"orchestration carries state/decision tokens: {sorted(found)}"
        )

    def test_orchestrator_forges_no_authoritative_artifact(self) -> None:
        tree = _module_tree(orchestration_module)
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
        assert forged == []

    def test_orchestrator_never_stores_into_governance_inputs(
        self,
    ) -> None:
        tree = _module_tree(orchestration_module)
        stores = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id in {"task", "execution", "lease", "record"}
        ]
        assert stores == []

    def test_decision_model_is_constructed_only_by_interpretation(
        self,
    ) -> None:
        """No layer other than the PM interpretation module references
        the decision model — the boundary is structural."""

        scanned: list[Path] = []
        for folder in ("router", "providers", "app", "protocol"):
            scanned.extend(sorted((REPO_ROOT / folder).glob("*.py")))

        for path in scanned:
            if path.name == "interpretation.py":
                continue
            referenced = _referenced_names(_tree(path))
            offenders = referenced & {"PMDecision", "PMDecisionKind",
                                      "PMInterpreter"}
            assert not offenders, (
                f"{path.name} references the decision model: "
                f"{sorted(offenders)}"
            )


# ------------------------------------------------------------------
# Provider / specialist execution boundary
# ------------------------------------------------------------------

class TestProviderExecutionBoundary:
    """Provider execution produces raw execution information only."""

    def test_provider_returns_execution_information_not_lifecycle_state(
        self, monkeypatch, tmp_path
    ) -> None:
        def fake_run(command, **kwargs):
            class _Completed:
                returncode = 0
                stdout = '{"type":"text","part":{"text":"inspected"}}\n'
                stderr = ""

            return _Completed()

        monkeypatch.setattr("providers.opencode.subprocess.run", fake_run)

        result = OpenCodeClient().run("inspect the repository", tmp_path)

        assert isinstance(result, OpenCodeResult)
        assert result.exit_code == 0
        assert result.success
        assert result.text == "inspected"

        # Execution information — not governance state or a decision.
        for attr in ("state", "status", "kind", "decision"):
            assert not hasattr(result, attr)
        assert not isinstance(
            result,
            (TaskArtifact, HandoffArtifact, ExecutionResult, PMDecision,
             DispatchRecord),
        )

    def test_provider_run_never_sees_or_changes_task_state(
        self, monkeypatch, tmp_path
    ) -> None:
        def fake_run(command, **kwargs):
            class _Completed:
                returncode = 0
                stdout = '{"type":"text","part":{"text":"ok"}}\n'
                stderr = ""

            return _Completed()

        monkeypatch.setattr("providers.opencode.subprocess.run", fake_run)

        task = _task()
        before = task

        OpenCodeClient().run("inspect the repository", tmp_path)

        assert task is before
        assert task.state is TaskState.IMPLEMENTING

    def test_provider_module_exposes_no_governance_authority(self) -> None:
        for name in (
            "TaskState", "HandoffState", "PMDecision", "PMDecisionKind",
            "PMInterpreter", "ExecutionResult", "ResultStatus",
        ):
            assert not hasattr(opencode_module, name), name


# ------------------------------------------------------------------
# Orchestration is mechanical
# ------------------------------------------------------------------

class TestOrchestratorIsMechanical:
    """The orchestrator dispatches, enforces leases, and transports —
    it never decides and never transitions."""

    def test_dispatch_returns_raw_execution_record(self, tmp_path) -> None:
        orchestrator, backend = _orchestrator()
        task, execution = _task(), _execution()

        jev_task = JEVTask(prompt="inspect", workdir=str(tmp_path))
        record = orchestrator.dispatch(
            task=task,
            execution=execution,
            jev_task=jev_task,
            lease_id=LEASE_ID,
            ttl=60.0,
            available_models=[_model()],
        )

        assert isinstance(record, DispatchRecord)
        assert isinstance(record.raw_result, JEVResult)
        assert record.raw_result.success
        assert record.raw_result.text == "ok"
        assert record.raw_result.exit_code == 0
        assert record.execution is execution
        # Execution/recovery metadata persisted with the record.
        assert record.lease_status.lease_state is LeaseState.RELEASED
        assert record.lease_status.execution_state is ExecutionState.FINISHED
        assert record.lease_status.task_id == TASK_ID
        # Mechanical dispatch reached the provider exactly once, with
        # the governing policy transported unchanged.
        assert len(backend.requests) == 1
        assert backend.requests[0]["policy"] is jev_task.policy
        # And the authoritative task was never touched.
        assert task.state is TaskState.IMPLEMENTING

    def test_record_carries_no_decision_or_lifecycle_state(
        self, tmp_path
    ) -> None:
        orchestrator, _ = _orchestrator()
        task, execution = _task(), _execution()

        record = _dispatch(orchestrator, task, execution,
                           workdir=str(tmp_path))

        # No field (nor the nested lease status) holds a task/handoff
        # lifecycle state or a decision of any kind.
        for value in (
            record.execution,
            record.lease_status,
            record.lease_status.lease,
            record.lease_status.lease_state,
            record.lease_status.execution_state,
            record.raw_result,
        ):
            assert not isinstance(value, (TaskState, HandoffState))
            assert not isinstance(value, (PMDecision, PMDecisionKind))
        assert not hasattr(record, "decision")
        assert not hasattr(record, "kind")
        with pytest.raises(FrozenInstanceError):
            record.raw_result = None  # type: ignore[misc]

    def test_failed_execution_is_returned_as_raw_state_not_a_decision(
        self, tmp_path
    ) -> None:
        orchestrator, backend = _orchestrator()
        task, execution = _task(), _execution()

        # Empty catalogue: the dispatch core reports a failure result.
        record = _dispatch(orchestrator, task, execution,
                           workdir=str(tmp_path), models=[])

        assert record.raw_result.success is False
        assert "No suitable free model" in (record.raw_result.error or "")
        assert backend.requests == []
        # The failure travelled upward as raw state — no decision, no
        # transition, and the lease slot is not stranded.
        assert not hasattr(record, "decision")
        assert task.state is TaskState.IMPLEMENTING
        assert record.lease_status.lease_state is LeaseState.RELEASED

    def test_malformed_request_is_refused_before_any_lease(
        self, tmp_path
    ) -> None:
        orchestrator, _ = _orchestrator()
        task, execution = _task(), _execution()

        with pytest.raises(OrchestrationError):
            orchestrator.dispatch(
                task=task,
                execution=execution,
                jev_task="not a JEV task",  # type: ignore[arg-type]
                lease_id=LEASE_ID,
                ttl=60.0,
                available_models=[_model()],
            )
        # Governance itself refuses a blank lease identity, still
        # without consuming a lease slot.
        with pytest.raises(ArtifactValidationError):
            _dispatch(orchestrator, task, execution,
                      workdir=str(tmp_path), lease_id="   ")

        assert orchestrator.lease_manager.status_for(task) is None

    def test_duplicate_dispatch_identity_is_refused(self, tmp_path) -> None:
        orchestrator, backend = _orchestrator()
        task, execution = _task(), _execution()

        _dispatch(orchestrator, task, execution, workdir=str(tmp_path))

        # Same lease identity again — a stale/replayed instruction.
        with pytest.raises(LeaseError):
            _dispatch(orchestrator, task, execution,
                      workdir=str(tmp_path))
        # A new lease identity over a known execution — a duplicate.
        with pytest.raises(DuplicateExecutionError):
            _dispatch(orchestrator, task, execution,
                      workdir=str(tmp_path), lease_id=f"{LEASE_ID}-002")

        # Only fresh identities over the concluded slot dispatch again.
        record = _dispatch(
            orchestrator, task,
            _execution(execution_id="EXEC-TASK-007-002",
                       idempotency_key="TASK-007-v1-exec-002"),
            workdir=str(tmp_path),
            lease_id=f"{LEASE_ID}-003",
        )
        assert record.raw_result.success
        assert len(backend.requests) == 2

    def test_pm_held_lease_blocks_dispatch(self, tmp_path) -> None:
        orchestrator, backend = _orchestrator()
        task = _task()

        # The Project Manager holds the slot for this task/version…
        orchestrator.lease_manager.acquire(
            task, _execution(), lease_id="LEASE-PM-HELD", ttl=60.0
        )

        # …so the orchestrator cannot dispatch a competing execution.
        with pytest.raises(DuplicateLeaseError):
            _dispatch(
                orchestrator, task,
                _execution(execution_id="EXEC-TASK-007-B",
                           idempotency_key="TASK-007-v1-exec-B"),
                workdir=str(tmp_path),
                lease_id=f"{LEASE_ID}-B",
            )
        assert backend.requests == []

    def test_lease_expiry_during_dispatch_requires_reconciliation(
        self, tmp_path
    ) -> None:
        # A deterministic clock that the execution "takes too long" on.
        clock = [1000.0]
        manager = ExecutionLeaseManager(clock=lambda: clock[0])

        class _SlowClient(_FakeOpenCodeClient):
            def run(self, prompt, workdir, *, model=None, policy=None):
                clock[0] += 120.0  # dispatch outlives the 60s lease term
                return super().run(prompt, workdir, model=model,
                                   policy=policy)

        orchestrator, _ = _orchestrator(
            client=_SlowClient(), lease_manager=manager
        )
        task, execution = _task(), _execution()

        # The lease term is an execution limit: expiry mid-dispatch
        # surfaces as a reconciliation requirement — never a silent
        # conclusion or a lifecycle decision.
        with pytest.raises(ReconciliationRequiredError):
            _dispatch(orchestrator, task, execution,
                      workdir=str(tmp_path))

        status = manager.status_for(task)
        assert status.lease_state is LeaseState.EXPIRED
        assert status.execution_state is ExecutionState.UNKNOWN
        assert status.requires_reconciliation

        # Any further dispatch stays refused until PM-side reconciliation.
        with pytest.raises(ReconciliationRequiredError):
            _dispatch(
                orchestrator, task,
                _execution(execution_id="EXEC-TASK-007-C",
                           idempotency_key="TASK-007-v1-exec-C"),
                workdir=str(tmp_path),
                lease_id=f"{LEASE_ID}-C",
            )

    def test_refused_policy_dispatch_never_executes_and_releases_lease(
        self, monkeypatch, tmp_path
    ) -> None:
        spawns: list = []

        def fake_run(command, **kwargs):
            spawns.append(command)
            raise AssertionError("must not spawn")

        monkeypatch.setattr("providers.opencode.subprocess.run", fake_run)

        orchestrator = Orchestrator(jev=JEV(client=OpenCodeClient()))
        task, execution = _task(), _execution()

        with pytest.raises(PolicyViolationError):
            _dispatch(
                orchestrator, task, execution, workdir=str(tmp_path),
                policy=ExecutionPolicy(edit=True),
            )

        assert spawns == []
        status = orchestrator.lease_manager.status_for(task)
        assert status is not None
        assert status.lease_state is LeaseState.RELEASED

    def test_orchestrator_exposes_no_interpretation_or_recovery_authority(
        self,
    ) -> None:
        # No interpret/decide/transition capability on the class…
        for name in (
            "interpret", "decide", "transition", "apply_decision",
            "complete", "reconcile", "authorize_replacement",
            "create_replacement", "mark_unknown",
        ):
            assert not hasattr(Orchestrator, name), name
        # …and no decision model in its module namespace.
        for name in ("PMInterpreter", "PMDecision", "PMDecisionKind",
                     "ResultStatus", "TaskState", "HandoffState"):
            assert not hasattr(orchestration_module, name), name

        # Recovery/authorization stays reachable only through the
        # governance lease model the orchestrator merely enforces.
        assert isinstance(Orchestrator.lease_manager, property)
        for name in ("reconcile", "authorize_replacement",
                     "create_replacement"):
            assert hasattr(ExecutionLeaseManager, name), name


# ------------------------------------------------------------------
# PM interpretation remains the authority boundary
# ------------------------------------------------------------------

class TestPMInterpretationIsTheBoundary:
    """The end-to-end chain: raw result -> specialist result ->
    PM interpretation -> decision, with authoritative state changing
    only when the Project Manager explicitly transitions it."""

    def test_full_chain_interprets_before_any_transition(
        self, tmp_path
    ) -> None:
        orchestrator, _ = _orchestrator()
        task, execution, project = _task(), _execution(), _project()
        original = _task()

        # 1. Execution produces a raw result; orchestration transports it.
        record = _dispatch(orchestrator, task, execution,
                           workdir=str(tmp_path))
        assert record.raw_result.success
        assert task == original

        # 2. The specialist structures the raw result with evidence.
        result = _specialist_result(record.raw_result)
        assert result.payload is record.raw_result
        assert task == original

        # 3. PM interpretation converts the result into a decision.
        decision = PMInterpreter().interpret(
            result, task, execution, project=project
        )
        assert decision.kind is PMDecisionKind.COMPLETE
        assert decision.result_status is ResultStatus.SUCCESS
        assert decision.covered_criteria == CRITERIA

        # 4. Interpretation changed nothing: the authoritative task
        #    remains untouched, and only an explicit PM act transitions it.
        assert task.state is TaskState.IMPLEMENTING
        transitioned = replace(task, state=TaskState.COMPLETED)
        assert task.state is TaskState.IMPLEMENTING
        assert transitioned.state is TaskState.COMPLETED

    def test_failed_raw_result_is_not_an_outcome_until_interpreted(
        self, tmp_path
    ) -> None:
        orchestrator, _ = _orchestrator()
        task, execution = _task(), _execution()

        record = _dispatch(orchestrator, task, execution,
                           workdir=str(tmp_path), models=[])
        # The record itself carries no outcome…
        assert not hasattr(record, "decision")
        assert task.state is TaskState.IMPLEMENTING

        # …only interpretation turns the raw result into a decision.
        decision = PMInterpreter().interpret(
            _specialist_result(record.raw_result), task, execution
        )
        assert decision.kind is PMDecisionKind.REROUTE
        assert decision.result_status is ResultStatus.FAILURE
        assert task.state is TaskState.IMPLEMENTING

    def test_raw_provider_success_cannot_claim_completion_without_evidence(
        self, tmp_path
    ) -> None:
        orchestrator, _ = _orchestrator()
        task, execution = _task(), _execution()

        record = _dispatch(orchestrator, task, execution,
                           workdir=str(tmp_path))
        assert record.raw_result.success  # process exit code 0

        # A raw success cannot become a completion claim: even the
        # result model refuses it before interpretation ever sees it.
        with pytest.raises(
            ArtifactValidationError, match="without required evidence"
        ):
            ExecutionResult(
                result_id=RESULT_ID,
                task_id=TASK_ID,
                task_version=TASK_VERSION,
                requirement_version=REQUIREMENT_VERSION,
                execution_id=EXECUTION_ID,
                correlation_id=CORRELATION_ID,
                status=ResultStatus.SUCCESS,
                acceptance_criteria=CRITERIA,
                evidence=(),
                summary="process exited cleanly",
                payload=record.raw_result,
            )
        assert task.state is TaskState.IMPLEMENTING


if __name__ == "__main__":
    pytest.main([__file__])
