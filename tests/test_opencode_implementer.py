"""MVP-005 focused tests — the OpenCode Implementer adapter.

Evidence produced here (entirely offline — the whole module runs under
an autouse guard that fails any live ``subprocess.run`` spawn, and the
injected JEV never fetches a model catalogue over the network):

- a valid generic :class:`~protocol.implementer.ImplementationTask`
  renders into the existing OpenCode/JEV execution request
  (:class:`router.jev.JEVTask`): the prompt carries the instruction
  plus the project/task/execution traceability, acceptance criteria,
  dependencies, and verification definition, while the workspace and
  the read-only execution policy travel as JEV configuration;
- a raw OpenCode/JEV execution result maps back into a valid generic
  :class:`~protocol.implementer.ImplementationReport` that passes
  :func:`~protocol.implementer.validate_report` — for successful,
  failed, and dispatch-blocked executions;
- traceability survives both directions: identities are bound to the
  handed-off task/execution, the evidence chain cites the task's own
  acceptance criteria and declared verification method, the result
  identity is derived from the execution identity, and the payload
  echoes the dispatched prompt with the backend execution metadata;
- invalid generic tasks and malformed/ambiguous backend results fail
  closed (``ArtifactValidationError`` / ``OpenCodeImplementerError``)
  instead of producing a dubious report; unsupported incomplete
  statuses (PARTIAL, NEEDS_USER) are never guessed from free text;
- the whole adapter runs against injected fakes (a fake
  ``OpenCodeClient`` behind a real JEV, plus raw ``JEVResult``
  objects) — no live OpenCode execution is required;
- OpenCode-specific behavior stays in the adapter module: the generic
  ``protocol.implementer`` contract still names no backend and imports
  governance scope only, the adapter imports no PM layer and
  references no PM decision model, and the adapter class holds no
  approval, decision, scheduling, or lifecycle-transition authority.
"""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import protocol.implementer as implementer_module
import router.opencode_implementer as adapter_module
from protocol.artifacts import (
    ArtifactValidationError,
    ExecutionArtifact,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
    VerificationDefinition,
)
from protocol.implementer import (
    Implementer,
    ImplementationReport,
    ImplementationTask,
    validate_report,
)
from protocol.results import ResultStatus
from providers.opencode import (
    OpenCodeClient,
    OpenCodeEvent,
    OpenCodeResult,
)
from providers.openrouter import OpenRouterModel
from router.jev import JEV, JEVResult, JEVTask
from router.opencode_implementer import (
    OpenCodeImplementer,
    OpenCodeImplementerError,
)
from router.policy import ExecutionPolicy, write_capabilities

REPO_ROOT = Path(__file__).resolve().parent.parent

PROJECT_ID = "AI Router / JEV"
PROJECT_VERSION = "v1.1-migration"
TASK_ID = "TASK-MVP-005"
TASK_VERSION = "v1"
REQUIREMENT_VERSION = "v1.1"
EXECUTION_ID = "EXEC-MVP-005-001"
CORRELATION_ID = "CORR-MVP-005-001"
IDEMPOTENCY_KEY = "TASK-MVP-005-v1-exec-001"
INSTRUCTION = "Implement the OpenCode adapter with focused tests."
METHOD = "Focused tests"
DEPENDENCY = "TASK-004"
MODEL_ID = "opencode/mvp005-free"

CRITERION_1 = "The adapter translates tasks and results in both directions."
CRITERION_2 = "Focused adapter tests and the full suite pass."
CRITERIA = (CRITERION_1, CRITERION_2)

DEFAULT_WORKDIR = "/srv/ai-router"


# ------------------------------------------------------------------
# Offline guard: no test in this module may spawn a live OpenCode
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_live_opencode(monkeypatch):
    """Fail any attempt to spawn a real OpenCode process."""

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "live OpenCode execution attempted in adapter unit tests"
        )

    monkeypatch.setattr("providers.opencode.subprocess.run", _blocked)


class _NoNetworkRouter:
    """Stand-in OpenRouter client that fails the test if reached.

    The adapter must pass its model catalogue through per dispatch;
    reaching this client would mean a live model-catalogue fetch.
    """

    def models(self):
        raise AssertionError(
            "model catalogue fetched over the network in unit tests"
        )


class _ExplodingJEV:
    """Execution boundary whose run() raises instead of returning."""

    def run(self, task, *, available_models=None):
        raise RuntimeError("backend exploded")


# ------------------------------------------------------------------
# Builders
# ------------------------------------------------------------------

def _model(model_id: str = MODEL_ID) -> OpenRouterModel:
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


def _project() -> ProjectArtifact:
    return ProjectArtifact(
        project_id=PROJECT_ID, project_version=PROJECT_VERSION
    )


def _task(**overrides) -> TaskArtifact:
    base = dict(
        task_id=TASK_ID,
        task_version=TASK_VERSION,
        requirement_version=REQUIREMENT_VERSION,
        state=TaskState.IMPLEMENTING,
        acceptance_criteria=CRITERIA,
        dependencies=(DEPENDENCY,),
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


def _oc_result(
    *, exit_code: int = 0, text: str = "done", stderr: str = ""
) -> OpenCodeResult:
    """Canned OpenCodeResult as the provider boundary would return it."""

    events = []
    if text:
        events.append(
            OpenCodeEvent(type="text", raw={"part": {"text": text}})
        )
    return OpenCodeResult(
        exit_code=exit_code, events=events, stdout=text, stderr=stderr
    )


class _FakeOpenCodeClient(OpenCodeClient):
    """Canned-result execution backend; records every execution request."""

    def __init__(self, result: OpenCodeResult | None = None) -> None:
        super().__init__()
        self._result = result if result is not None else _oc_result()
        self.requests: list[dict] = []

    def run(self, prompt, workdir, *, model=None, policy=None):
        self.requests.append(
            {
                "prompt": prompt,
                "workdir": str(workdir),
                "model": model,
                "policy": policy,
            }
        )
        return self._result

    def can_route(self, model_id: str) -> bool:
        return True


def _raw(prompt: str, **overrides) -> JEVResult:
    """A raw JEVResult as the dispatch core would return it."""

    base = dict(
        success=True,
        prompt=prompt,
        model=MODEL_ID,
        exit_code=0,
        text="implemented; focused tests pass",
        events=[],
        error=None,
    )
    base.update(overrides)
    return JEVResult(**base)


def _adapter(
    *,
    client: OpenCodeClient | None = None,
    models: list | None = None,
    workdir: str = DEFAULT_WORKDIR,
    jev: object | None = None,
) -> OpenCodeImplementer:
    """An adapter over a real JEV with an injected fake backend.

    ``_NoNetworkRouter`` proves the catalogue is passed through per
    dispatch rather than fetched; ``models`` defaults to one free
    model so a dispatch always reaches the fake client.
    """

    if jev is None:
        jev = JEV(
            client=client if client is not None else _FakeOpenCodeClient(),
            router_client=_NoNetworkRouter(),  # type: ignore[arg-type]
        )
    return OpenCodeImplementer(
        workdir=workdir,
        jev=jev,  # type: ignore[arg-type]
        available_models=[_model()] if models is None else models,
    )


# ------------------------------------------------------------------
# 1. Valid generic task -> OpenCode execution request
# ------------------------------------------------------------------

class TestGenericTaskToOpenCodeRequest:
    """The input direction: the generic contract record becomes the
    execution request the existing dispatch core already understands."""

    def test_request_renders_instruction_and_traceability(self) -> None:
        adapter = _adapter()
        task = _implementation_task()

        request = adapter.build_jev_task(task)

        assert isinstance(request, JEVTask)
        prompt = request.prompt
        # The requested work travels verbatim…
        assert INSTRUCTION in prompt
        # …alongside every traceability identity and the task content
        # the backend needs to perform exactly this work.
        for token in (
            PROJECT_ID,
            PROJECT_VERSION,
            TASK_ID,
            TASK_VERSION,
            REQUIREMENT_VERSION,
            EXECUTION_ID,
            CORRELATION_ID,
            IDEMPOTENCY_KEY,
            DEPENDENCY,
            CRITERION_1,
            CRITERION_2,
            f"Verification method: {METHOD}",
        ):
            assert token in prompt, token

    def test_request_carries_workspace_and_read_only_policy(self) -> None:
        adapter = _adapter(workdir="/tmp/adapter-workspace")
        request = adapter.build_jev_task(_implementation_task())

        # Workspace and policy travel as JEV configuration supplied by
        # the adapter's caller and JEV's fail-closed default — the
        # generic contract carries no such field and gains none.
        assert request.workdir == "/tmp/adapter-workspace"
        assert isinstance(request.policy, ExecutionPolicy)
        assert write_capabilities(request.policy) == ()

    def test_invalid_generic_task_never_reaches_the_backend(self) -> None:
        client = _FakeOpenCodeClient()
        adapter = _adapter(client=client)

        for bad in ("not-a-task", None, 7, {"instruction": "x"}):
            with pytest.raises(ArtifactValidationError, match="task"):
                adapter.execute(bad)  # type: ignore[arg-type]
            with pytest.raises(ArtifactValidationError, match="task"):
                adapter.build_jev_task(bad)  # type: ignore[arg-type]

        assert client.requests == []

    def test_blank_workspace_configuration_fails_closed(self) -> None:
        for bad in ("", "   ", None):
            with pytest.raises(OpenCodeImplementerError, match="workdir"):
                _adapter(workdir=bad)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# 2. OpenCode result -> valid generic ImplementationReport
# ------------------------------------------------------------------

class TestOpenCodeResultToGenericReport:
    """The output direction: every producible backend outcome becomes a
    generic report that passes the contract's own consumption check."""

    def test_successful_execution_maps_to_valid_report(self) -> None:
        client = _FakeOpenCodeClient(
            _oc_result(exit_code=0, text="implemented; focused tests pass")
        )
        adapter = _adapter(client=client)
        task = _implementation_task()

        report = adapter.execute(task)

        assert isinstance(report, ImplementationReport)
        validate_report(report, task)
        assert report.is_success
        assert not report.is_failure and not report.is_incomplete
        assert report.implementer == "opencode"
        assert report.result is not None
        assert report.result.status is ResultStatus.SUCCESS
        assert report.result.acceptance_criteria == CRITERIA
        assert report.result.covered_criteria == CRITERIA
        assert len(report.result.evidence) == len(CRITERIA)
        assert report.detail == "implemented; focused tests pass"
        # The request actually travelled through the injected backend.
        assert len(client.requests) == 1

    def test_failed_execution_maps_to_failure_report(self) -> None:
        client = _FakeOpenCodeClient(
            _oc_result(exit_code=1, text="focused tests failed")
        )
        adapter = _adapter(client=client)
        task = _implementation_task()

        report = adapter.execute(task)

        validate_report(report, task)
        assert report.is_failure
        assert report.result is not None
        assert report.result.status is ResultStatus.FAILURE
        assert report.result.evidence == ()
        assert "focused tests failed" in report.detail
        assert report.result.payload["exit_code"] == 1

    def test_dispatch_error_maps_to_blocked_report(self) -> None:
        # Empty catalogue: the dispatch core reports a dispatch-level
        # error result (execution never produced an outcome).
        client = _FakeOpenCodeClient()
        adapter = _adapter(client=client, models=[])
        task = _implementation_task()

        report = adapter.execute(task)

        validate_report(report, task)
        assert report.is_incomplete
        assert report.status is ResultStatus.BLOCKED
        assert report.result is not None
        assert report.result.status is ResultStatus.BLOCKED
        assert report.result.evidence == ()
        assert "No suitable free model" in report.detail
        assert client.requests == []  # nothing was executed

    def test_backend_exception_propagates_without_a_report(self) -> None:
        # An execution that produced no result yields an exception,
        # never a fabricated report (fail closed).
        adapter = _adapter(jev=_ExplodingJEV())

        with pytest.raises(RuntimeError, match="backend exploded"):
            adapter.execute(_implementation_task())

    def test_adapter_runs_entirely_against_the_injected_backend(
        self,
    ) -> None:
        # Autouse guard blocks subprocess spawns and _NoNetworkRouter
        # blocks catalogue fetches: this full success path proves the
        # normal unit-test suite needs no live OpenCode at all.
        client = _FakeOpenCodeClient(_oc_result(exit_code=0, text="ok"))
        adapter = _adapter(client=client)

        report = adapter.execute(_implementation_task())

        assert report.is_success
        assert len(client.requests) == 1


# ------------------------------------------------------------------
# 3. Traceability preservation in both directions
# ------------------------------------------------------------------

class TestTraceabilityPreservation:
    """Project/task/execution/result identity and the evidence chain
    survive the translation unchanged."""

    @pytest.mark.parametrize(
        "outcome",
        [
            {"exit_code": 0, "text": "done"},
            {"exit_code": 2, "text": "compile error"},
        ],
    )
    def test_report_binds_the_handed_off_identities(
        self, outcome: dict
    ) -> None:
        client = _FakeOpenCodeClient(_oc_result(**outcome))
        adapter = _adapter(client=client)
        task = _implementation_task()

        report = adapter.execute(task)

        assert report.task_id == TASK_ID
        assert report.task_version == TASK_VERSION
        assert report.requirement_version == REQUIREMENT_VERSION
        assert report.execution_id == EXECUTION_ID
        assert report.correlation_id == CORRELATION_ID
        assert report.result is not None
        assert report.result.result_id == f"RESULT-{EXECUTION_ID}"

    def test_evidence_chain_cites_task_criteria_and_verification(
        self,
    ) -> None:
        adapter = _adapter(client=_FakeOpenCodeClient())
        task = _implementation_task()

        report = adapter.execute(task)

        assert report.result is not None
        seen_ids = set()
        for index, item in enumerate(report.result.evidence, start=1):
            assert item.evidence_id.startswith(f"EVID-{EXECUTION_ID}-")
            assert item.evidence_id not in seen_ids
            seen_ids.add(item.evidence_id)
            assert item.task_id == TASK_ID
            assert item.task_version == TASK_VERSION
            assert item.execution_id == EXECUTION_ID
            assert item.requirement_version == REQUIREMENT_VERSION
            assert item.acceptance_criterion in CRITERIA
            # The task's declared verification method always wins.
            assert item.verification_method == METHOD
            assert item.summary
        # The chain validates against the handed-off artifacts.
        validate_report(report, task)

    def test_payload_echoes_the_dispatched_prompt_and_metadata(
        self,
    ) -> None:
        client = _FakeOpenCodeClient()
        adapter = _adapter(client=client)
        task = _implementation_task()
        request = adapter.build_jev_task(task)

        report = adapter.execute(task)

        assert report.result is not None
        payload = report.result.payload
        assert payload["backend"] == "opencode"
        assert payload["model"] == MODEL_ID
        assert payload["exit_code"] == 0
        assert payload["success"] is True
        assert payload["dispatch_error"] is None
        # The output record echoes the input request: traceability
        # survives the round trip.
        assert payload["prompt"] == request.prompt
        assert client.requests[0]["prompt"] == request.prompt

    def test_report_round_trips_as_json_with_backend_payload(
        self,
    ) -> None:
        adapter = _adapter(client=_FakeOpenCodeClient())
        report = adapter.execute(_implementation_task())

        # Backend metadata stays JSON-representable inside the payload,
        # so the generic record serializes without leaking live objects.
        text = report.to_json()
        assert ImplementationReport.from_json(text) == report
        assert json.loads(text)["record"] == "implementation-report"

    def test_execution_never_mutates_the_task_or_produces_a_state(
        self,
    ) -> None:
        adapter = _adapter(client=_FakeOpenCodeClient())
        task = _implementation_task()
        original = _task()

        report = adapter.execute(task)

        # Requested work stays untouched; the report is execution
        # metadata only and is frozen.
        assert task.task == original
        assert task.task.state is TaskState.IMPLEMENTING
        with pytest.raises(FrozenInstanceError):
            report.status = ResultStatus.FAILURE  # type: ignore[misc]
        # No decision or lifecycle authority rides along on the record.
        for attr in ("decision", "kind", "state", "transition"):
            assert not hasattr(report, attr)


# ------------------------------------------------------------------
# 4. Fail closed: malformed/ambiguous results and invalid tasks
# ------------------------------------------------------------------

class TestAmbiguousResultsFailClosed:
    """An execution outcome that cannot be mapped safely is refused
    instead of being turned into a dubious report."""

    @pytest.mark.parametrize(
        "foreign", ["not-a-result", None, 7, {"success": True}]
    )
    def test_foreign_result_object_is_rejected(self, foreign: object) -> None:
        adapter = _adapter()
        with pytest.raises(OpenCodeImplementerError, match="JEVResult"):
            adapter.build_report(_implementation_task(), foreign)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "success,exit_code",
        [(True, 3), (False, 0), (True, -9)],
    )
    def test_success_flag_contradicting_exit_code_is_rejected(
        self, success: bool, exit_code: int
    ) -> None:
        adapter = _adapter()
        task = _implementation_task()
        raw = _raw(
            adapter.build_jev_task(task).prompt,
            success=success,
            exit_code=exit_code,
        )
        with pytest.raises(OpenCodeImplementerError, match="contradicts"):
            adapter.build_report(task, raw)

    @pytest.mark.parametrize("exit_code", [0, 2])
    def test_dispatch_error_also_claiming_a_process_exit_is_rejected(
        self, exit_code: int
    ) -> None:
        adapter = _adapter()
        task = _implementation_task()
        raw = _raw(
            adapter.build_jev_task(task).prompt,
            success=False,
            exit_code=exit_code,
            error="provider timeout",
        )
        with pytest.raises(OpenCodeImplementerError):
            adapter.build_report(task, raw)

    @pytest.mark.parametrize("error", ["", "   "])
    def test_blank_dispatch_error_is_rejected(self, error: str) -> None:
        adapter = _adapter()
        task = _implementation_task()
        raw = _raw(
            adapter.build_jev_task(task).prompt,
            success=False,
            exit_code=-1,
            error=error,
        )
        with pytest.raises(OpenCodeImplementerError, match="no message"):
            adapter.build_report(task, raw)

    @pytest.mark.parametrize("exit_code", ["0", None, 1.0, True])
    def test_non_integer_exit_code_is_rejected(self, exit_code: object) -> None:
        adapter = _adapter()
        task = _implementation_task()
        raw = _raw(
            adapter.build_jev_task(task).prompt,
            success=False,
            exit_code=exit_code,  # type: ignore[arg-type]
        )
        with pytest.raises(OpenCodeImplementerError, match="not an integer"):
            adapter.build_report(task, raw)

    def test_result_not_answering_the_dispatched_request_is_rejected(
        self,
    ) -> None:
        adapter = _adapter()
        task = _implementation_task()
        raw = _raw("some other request's prompt")
        with pytest.raises(
            OpenCodeImplementerError, match="does not echo"
        ):
            adapter.build_report(task, raw)

    def test_invalid_task_is_rejected_by_the_report_builder(self) -> None:
        adapter = _adapter()
        prompt = adapter.build_jev_task(_implementation_task()).prompt
        raw = _raw(prompt)
        for bad in ("task", None, 42):
            with pytest.raises(ArtifactValidationError, match="task"):
                adapter.build_report(bad, raw)  # type: ignore[arg-type]

    def test_no_ambiguous_result_ever_returns_a_report(self) -> None:
        adapter = _adapter()
        task = _implementation_task()
        prompt = adapter.build_jev_task(task).prompt
        ambiguous = [
            _raw(prompt, success=True, exit_code=5),
            _raw(prompt, success=False, exit_code=0),
            _raw(prompt, success=True, exit_code=-1, error="boom"),
            _raw(prompt, success=False, exit_code=0, error="boom"),
            _raw(prompt, success=False, exit_code=-1, error=" "),
            _raw("foreign prompt"),
        ]
        for raw in ambiguous:
            with pytest.raises(OpenCodeImplementerError):
                adapter.build_report(task, raw)


# ------------------------------------------------------------------
# 5. Incomplete outcomes: supported where the backend can signal them,
#    never guessed otherwise
# ------------------------------------------------------------------

class TestIncompleteOutcomeMapping:
    """BLOCKED is mechanically derivable from a dispatch-level error;
    PARTIAL and NEEDS_USER have no signal on this boundary and are
    never invented from narrative content."""

    @pytest.mark.parametrize(
        "outcome,expected",
        [
            ({"exit_code": 0, "text": "done"}, ResultStatus.SUCCESS),
            ({"exit_code": 1, "text": "broken"}, ResultStatus.FAILURE),
        ],
    )
    def test_exit_code_outcomes_map_exactly(
        self, outcome: dict, expected: ResultStatus
    ) -> None:
        client = _FakeOpenCodeClient(_oc_result(**outcome))
        adapter = _adapter(client=client)

        report = adapter.execute(_implementation_task())

        assert report.status is expected
        validate_report(report, _implementation_task())

    def test_blocked_outcome_is_representable_as_incomplete(self) -> None:
        adapter = _adapter(client=_FakeOpenCodeClient(), models=[])

        report = adapter.execute(_implementation_task())

        assert report.is_incomplete
        assert (
            report.is_success + report.is_failure + report.is_incomplete == 1
        )
        # A blocked report carries its structured result too, so the
        # backend metadata is never lost for incomplete executions.
        assert report.result is not None
        assert report.result.payload["dispatch_error"]

    def test_partial_and_needs_user_are_never_guessed(self) -> None:
        # Every producible raw shape on the OpenCode/JEV boundary maps
        # into the closed subset {SUCCESS, FAILURE, BLOCKED}; the two
        # statuses this boundary cannot signal are never fabricated.
        adapter = _adapter()
        task = _implementation_task()
        prompt = adapter.build_jev_task(task).prompt
        shapes = [
            _raw(prompt),
            _raw(prompt, success=False, exit_code=1, text="failed"),
            _raw(prompt, success=False, exit_code=-1, error="blocked"),
        ]
        produced = {adapter.build_report(task, raw).status for raw in shapes}
        assert produced <= {
            ResultStatus.SUCCESS,
            ResultStatus.FAILURE,
            ResultStatus.BLOCKED,
        }
        assert ResultStatus.PARTIAL not in produced
        assert ResultStatus.NEEDS_USER not in produced


# ------------------------------------------------------------------
# 6. Isolation: backend-specific behavior stays out of the contract,
#    and no PM authority leaks into the adapter
# ------------------------------------------------------------------

def _source(module) -> str:
    path = Path(inspect.getsourcefile(module) or "")
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


class TestBackendIsolationAndAuthorityBoundaries:
    """The generic contract stays backend-independent, the adapter
    stays OpenCode-specific, and neither gains PM authority."""

    def test_adapter_satisfies_the_generic_implementer_interface(
        self,
    ) -> None:
        assert isinstance(_adapter(), Implementer)
        assert isinstance(
            OpenCodeImplementer(workdir=DEFAULT_WORKDIR), Implementer
        )

    def test_generic_contract_stays_backend_agnostic(self) -> None:
        source = _source(implementer_module)
        lowered = source.lower()
        # The contract names no OpenCode/JEV backend and no adapter…
        assert "opencode" not in lowered
        assert "jev" not in lowered
        assert "OpenCodeImplementer" not in source
        # …and still imports governance scope only.
        roots = _imported_roots(source)
        assert roots <= {
            "__future__", "dataclasses", "json", "typing", "protocol",
        }, sorted(roots)

    def test_opencode_logic_lives_in_the_adapter_module(self) -> None:
        source = _source(adapter_module)
        assert "OpenCode" in source
        assert "JEV" in source
        assert "router.jev" in source

    def test_adapter_imports_execution_and_governance_not_pm(self) -> None:
        roots = _imported_roots(_source(adapter_module))
        # It may depend on the generic contract and the OpenCode/JEV
        # dispatch core — never on the PM layer or directly on a
        # provider.
        assert roots <= {
            "__future__", "pathlib", "typing", "protocol", "router",
        }, sorted(roots)
        assert "protocol.implementer" in _source(adapter_module)

    def test_adapter_references_no_pm_decision_model(self) -> None:
        source = _source(adapter_module)
        for token in ("PMDecision", "PMDecisionKind", "PMInterpreter"):
            assert token not in source, token

    def test_adapter_holds_no_lifecycle_or_pm_authority(self) -> None:
        for name in (
            "interpret",
            "decide",
            "approve",
            "transition",
            "apply_decision",
            "complete",
            "schedule",
            "resolve_dependencies",
            "reconcile",
            "authorize_replacement",
        ):
            assert not hasattr(OpenCodeImplementer, name), name
        for name in ("TaskState", "HandoffState"):
            assert not hasattr(adapter_module, name), name

    def test_pm_source_scan_still_sees_no_backend_in_pm(self) -> None:
        # The PM layer stays uncoupled from OpenCode: no pm/ module
        # may import the adapter or the dispatch core.
        for path in sorted((REPO_ROOT / "pm").glob("*.py")):
            roots = _imported_roots(path.read_text(encoding="utf-8"))
            assert not roots & {"router", "providers"}, (
                f"{path.name} imports execution code: {sorted(roots)}"
            )


if __name__ == "__main__":
    pytest.main([__file__])
