"""MVP-001 focused tests — PM runtime, proposal path, and approval gate.

Evidence produced here (offline only — no network, model, provider, or
real backend call):

- the executable PM entry point accepts a user text request and
  produces a structured, machine-readable PM proposal built from the
  existing V1.1 artifacts (``ProjectArtifact`` / ``TaskArtifact`` /
  exact ``TaskState`` vocabulary / correlation ID / idempotency key);
- the proposal is created and persisted *before* any implementation
  handoff can be attempted, and re-proposing the same request is
  idempotent (an approval is never dropped);
- the rejection/no-approval path: a missing or unapproved proposal is
  refused by the PM-side gate and the backend callable is never
  invoked;
- PM remains the authoritative owner of project/task state: proposing,
  approving, and handing off rewrite no PM-owned register, expose no
  lifecycle-transition API, and leave the proposed task ``PENDING``;
- the PM runtime imports no execution layer, hard-codes no execution
  backend, and contains no permission-granting primitive — approval
  authorizes handing off exactly one proposal, nothing more;
- ``python -m pm`` works as the CLI entry point and fails closed on
  blank requests and unknown proposal identities.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pm import PMRuntime, PMProposal, ProjectContext
from pm.errors import (
    PMContextError,
    PMRuntimeError,
    ProposalAlreadyApprovedError,
    ProposalNotFoundError,
    ProposalNotApprovedError,
)
from pm.proposal import (
    PROPOSAL_ID_RE,
    PROPOSAL_SCHEMA,
    PROPOSAL_SCHEMA_VERSION,
    ApprovalRecord,
)
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUEST = "Add a --version flag to the PM CLI."
OTHER_REQUEST = "Document the PM proposal flow in the README."

PROJECT_ID = "AI Router / JEV"
PROJECT_VERSION = "v1.1-migration"
PROTOCOL_VERSION = "V1.1"

#: Identities that look well-formed but address no stored proposal.
UNKNOWN_VALID_ID = "PROP-0000000000000000"

PROJECT_STATE_MD = f"""# AI Router — Project State

## Protocol

Protocol: Interrogator AI-Assisted Project Lifecycle Protocol
Protocol Version: {PROTOCOL_VERSION}
Protocol Status: IMPLEMENTATION_READY

## Project

Project: {PROJECT_ID}
Platform: SteamOS / Steam Deck
Current Project State: IMPLEMENTING

## Inherited Baseline

Baseline Branch: master
Migration Branch:
protocol-v1.1-migration
"""

MASTER_PLAN_MD = f"""# AI Router / JEV — Master Plan

- **Protocol:** Interrogator AI-Assisted Project Lifecycle Protocol V1.1
- **Project version:** {PROJECT_VERSION}
- **Branch:** protocol-v1.1-migration
"""

TASKS_MD = """# AI Router — V1.1 PM Task Register

Protocol: Interrogator AI-Assisted Project Lifecycle Protocol
Protocol Version: V1.1
Project: AI Router / JEV

## TASK-001 — Baseline

State: COMPLETED

---

## TASK-002 — Artifact Model

State: IMPLEMENTING
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A minimal project root with the PM-owned records."""

    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "PROJECT_STATE.md").write_text(
        PROJECT_STATE_MD, encoding="utf-8"
    )
    (tmp_path / "project" / "TASKS.md").write_text(
        TASKS_MD, encoding="utf-8"
    )
    (tmp_path / "MASTER_PLAN.md").write_text(MASTER_PLAN_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def runtime(root: Path) -> PMRuntime:
    return PMRuntime(root=root)


class _RecordingBackend:
    """Opaque stand-in for any implementation backend.

    Records every invocation as ``(args, kwargs)`` so the tests can
    prove exactly what the PM gate handed over — and that the backend
    was never reached when it should not have been.
    """

    def __init__(self, value: object = "backend-output") -> None:
        self.value = value
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.value

    @property
    def proposals(self) -> list[object]:
        return [args[0] for args, _ in self.calls]


def _store_path(root: Path, proposal: PMProposal) -> Path:
    return root / "project" / "proposals" / f"{proposal.proposal_id}.json"


def _pm_sources() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "pm").glob("*.py"))
    }


def _imported_modules(text: str) -> list[str]:
    tree = ast.parse(text)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    return imported


def _run_cli(*args: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pm", *args, "--root", str(root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ------------------------------------------------------------------
# Normal proposal path
# ------------------------------------------------------------------

class TestProposalPath:
    """A user text request becomes a persisted, structured PM proposal."""

    def test_context_loads_pm_owned_records(self, root: Path) -> None:
        context = ProjectContext.load(root)

        assert context.project == ProjectArtifact(
            project_id=PROJECT_ID, project_version=PROJECT_VERSION
        )
        assert context.protocol_version == PROTOCOL_VERSION
        assert context.requirement_version == PROTOCOL_VERSION
        assert context.protocol_status == "IMPLEMENTATION_READY"
        assert context.current_project_state == "IMPLEMENTING"
        assert context.migration_branch == "protocol-v1.1-migration"
        assert context.task_ids == ("TASK-001", "TASK-002")
        assert all(isinstance(entry.state, TaskState) for entry in context.tasks)

    def test_context_loads_the_real_project_records(self) -> None:
        context = ProjectContext.load(REPO_ROOT)

        assert context.project.project_id == PROJECT_ID
        assert context.project.project_version == PROJECT_VERSION
        assert context.requirement_version == PROTOCOL_VERSION
        # The authoritative register is read, not rewritten.
        assert {"TASK-001", "TASK-008"} <= set(context.task_ids)
        assert all(isinstance(entry.state, TaskState) for entry in context.tasks)

    def test_context_fails_closed_when_a_record_is_missing(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / "TASKS.md").write_text(
            TASKS_MD, encoding="utf-8"
        )
        (tmp_path / "MASTER_PLAN.md").write_text(
            MASTER_PLAN_MD, encoding="utf-8"
        )

        with pytest.raises(PMContextError, match="PROJECT_STATE"):
            ProjectContext.load(tmp_path)

    def test_context_rejects_an_invented_register_state(
        self, root: Path
    ) -> None:
        (root / "project" / "TASKS.md").write_text(
            TASKS_MD.replace("State: COMPLETED", "State: IN_PROGRESS"),
            encoding="utf-8",
        )

        with pytest.raises(PMContextError, match="non-V1.1 state"):
            ProjectContext.load(root)

    def test_context_rejects_a_register_entry_without_state(
        self, root: Path
    ) -> None:
        (root / "project" / "TASKS.md").write_text(
            "# register\n\n## TASK-009 — Half-written entry\n",
            encoding="utf-8",
        )

        with pytest.raises(PMContextError, match="without an exact V1.1 State"):
            ProjectContext.load(root)

    def test_propose_builds_a_structured_v11_proposal(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)

        assert isinstance(proposal, PMProposal)
        assert isinstance(proposal.project, ProjectArtifact)
        assert proposal.project.project_id == PROJECT_ID
        assert proposal.requirement_version == PROTOCOL_VERSION
        assert proposal.request == REQUEST
        assert proposal.summary
        # V1.1 identity vocabulary.
        assert PROPOSAL_ID_RE.match(proposal.proposal_id)
        digest = proposal.proposal_id.split("-", 1)[1]
        assert proposal.correlation_id == f"CORR-{digest}"
        assert proposal.idempotency_key
        # The proposed work is a real V1.1 task artifact, still PENDING.
        assert isinstance(proposal.task, TaskArtifact)
        assert proposal.task.state is TaskState.PENDING
        assert proposal.task.requirement_version == proposal.requirement_version
        assert proposal.task.acceptance_criteria
        assert REQUEST in proposal.task.acceptance_criteria[0]
        assert proposal.task.verification is not None
        assert proposal.task.verification.method
        # Grounded in the loaded project context, and unapproved.
        assert proposal.context.project == proposal.project
        assert proposal.context.task_ids == ("TASK-001", "TASK-002")
        assert proposal.approval is None
        assert proposal.is_approved is False

    def test_proposal_is_machine_readable_and_round_trips(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        proposal = runtime.propose(REQUEST)
        path = _store_path(root, proposal)

        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema"] == PROPOSAL_SCHEMA
        assert data["schema_version"] == PROPOSAL_SCHEMA_VERSION
        assert data["proposal_id"] == proposal.proposal_id
        assert data["approved"] is False
        assert data["approval"] is None
        # Exact V1.1 vocabulary on disk — nothing invented.
        TaskState(data["task"]["state"])
        assert data["task"]["state"] == "PENDING"
        assert data["project"]["project_id"] == PROJECT_ID
        assert isinstance(data["context"]["tasks"], list)
        assert data["context"]["tasks"][0]["task_id"] == "TASK-001"

        assert PMProposal.from_json(path.read_text(encoding="utf-8")) == proposal

    def test_propose_is_idempotent_and_preserves_approval(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first = runtime.propose(REQUEST)
        approved = runtime.approve(first.proposal_id, approver="pm")

        second = runtime.propose(REQUEST)
        assert second.proposal_id == first.proposal_id
        assert second.is_approved
        assert second.approval == approved.approval
        assert len(list((root / "project" / "proposals").glob("*.json"))) == 1

        # A different request is a different proposal.
        other = runtime.propose(OTHER_REQUEST)
        assert other.proposal_id != first.proposal_id
        assert other.is_approved is False

    def test_propose_rejects_a_blank_request(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        for blank in ("", "   ", "\n\t"):
            with pytest.raises(ArtifactValidationError, match="non-empty"):
                runtime.propose(blank)
        with pytest.raises(ArtifactValidationError, match="non-empty"):
            runtime.propose(42)  # type: ignore[arg-type]

        assert not (root / "project" / "proposals").exists()

    def test_a_proposal_never_asserts_implemented_work(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)

        with pytest.raises(ArtifactValidationError, match="PENDING"):
            replace(proposal, task=replace(proposal.task, state=TaskState.COMPLETED))

        # Invented lifecycle states are refused by the V1.1 artifact
        # model long before they could reach a proposal.
        with pytest.raises(ArtifactValidationError):
            replace(proposal, task=replace(proposal.task, state="PROPOSED"))

    def test_a_proposal_cannot_be_relabelled_to_another_project(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)

        with pytest.raises(ArtifactValidationError, match="context"):
            replace(
                proposal,
                project=ProjectArtifact(project_id="other", project_version="v9"),
            )


# ------------------------------------------------------------------
# Approval gate: rejection / prevention when approval is absent
# ------------------------------------------------------------------

class TestApprovalGate:
    """Implementation cannot be reached without an existing, approved
    proposal — the fail-closed PM-side gate."""

    def test_handoff_requires_an_existing_proposal(
        self, runtime: PMRuntime
    ) -> None:
        backend = _RecordingBackend()

        with pytest.raises(ProposalNotFoundError):
            runtime.handoff(UNKNOWN_VALID_ID, backend)

        assert backend.calls == []

    def test_an_unapproved_proposal_never_reaches_the_backend(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        proposal = runtime.propose(REQUEST)
        backend = _RecordingBackend()

        # The proposal exists on disk, yet the handoff is still refused.
        assert _store_path(root, proposal).is_file()
        with pytest.raises(ProposalNotApprovedError, match="not approved"):
            runtime.handoff(proposal.proposal_id, backend)

        assert backend.calls == []
        assert runtime.get(proposal.proposal_id).is_approved is False

    def test_handoff_refuses_malformed_proposal_identities(
        self, runtime: PMRuntime
    ) -> None:
        backend = _RecordingBackend()

        for identity in (
            "not-a-proposal",
            "../PROJECT_STATE",
            "../../etc/passwd",
            "prop-0000000000000000",
            "",
        ):
            with pytest.raises(ProposalNotFoundError):
                runtime.handoff(identity, backend)

        assert backend.calls == []

    def test_handoff_requires_a_callable_backend(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)
        runtime.approve(proposal.proposal_id, approver="pm")

        with pytest.raises(PMRuntimeError, match="callable"):
            runtime.handoff(proposal.proposal_id, object())  # type: ignore[arg-type]

    def test_an_approved_proposal_reaches_the_backend(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)
        backend = _RecordingBackend(value="raw backend output")

        # Refused first…
        with pytest.raises(ProposalNotApprovedError):
            runtime.handoff(proposal.proposal_id, backend)

        # …then, after the explicit PM approval, handed over.
        runtime.approve(proposal.proposal_id, approver="pm", note="mvp-001")
        outcome = runtime.handoff(proposal.proposal_id, backend)

        assert outcome == "raw backend output"
        assert len(backend.calls) == 1
        args, kwargs = backend.calls[0]
        assert len(args) == 1 and kwargs == {}
        handed_over = args[0]
        assert isinstance(handed_over, PMProposal)
        assert handed_over.proposal_id == proposal.proposal_id
        assert handed_over.is_approved
        assert handed_over.task.state is TaskState.PENDING

    def test_an_approval_cannot_be_overwritten(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        proposal = runtime.propose(REQUEST)
        first = runtime.approve(proposal.proposal_id, approver="pm-one")

        with pytest.raises(ProposalAlreadyApprovedError):
            runtime.approve(proposal.proposal_id, approver="pm-two")

        stored = runtime.get(proposal.proposal_id)
        assert stored.approval == first.approval
        assert stored.approval.approver == "pm-one"
        on_disk = json.loads(_store_path(root, proposal).read_text(encoding="utf-8"))
        assert on_disk["approval"]["approver"] == "pm-one"


# ------------------------------------------------------------------
# Backend independence and permission posture
# ------------------------------------------------------------------

class TestBackendIndependenceAndPermissions:
    """The PM runtime is backend-neutral and grants nothing."""

    def test_pm_package_imports_no_execution_layer(self) -> None:
        for name, text in _pm_sources().items():
            roots = {part.split(".")[0] for part in _imported_modules(text)}
            assert not roots & {"router", "providers"}, (
                f"{name} imports an execution layer: {sorted(roots)}"
            )

    def test_pm_package_hard_codes_no_execution_backend(self) -> None:
        banned = (
            "opencode",
            "codex",
            "openrouter",
            "anthropic",
            "ollama",
        )
        for name, text in _pm_sources().items():
            lowered = text.lower()
            offenders = [token for token in banned if token in lowered]
            assert offenders == [], f"{name} names a backend: {offenders}"

    def test_pm_package_contains_no_permission_granting_primitive(self) -> None:
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
        for name, text in _pm_sources().items():
            lowered = text.lower()
            offenders = [token for token in banned if token in lowered]
            assert offenders == [], f"{name} holds a forbidden primitive: {offenders}"

    def test_proposal_and_approval_carry_no_permission_fields(self) -> None:
        proposal_fields = set(PMProposal.__dataclass_fields__)
        assert not proposal_fields & {
            "policy",
            "permission",
            "permissions",
            "credential",
            "credentials",
            "token",
            "workspace",
            "shell",
            "backend",
            "network",
        }
        assert set(ApprovalRecord.__dataclass_fields__) == {
            "approver",
            "approved_at",
            "note",
        }

    def test_approval_changes_nothing_but_the_approval(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)
        approved = runtime.approve(proposal.proposal_id, approver="pm")

        before = proposal.to_dict()
        after = approved.to_dict()
        assert set(before) == set(after)
        changed = {key for key in before if before[key] != after[key]}
        assert changed == {"approval", "approved"}
        assert approved.task == proposal.task
        assert approved.context == proposal.context

    def test_handoff_passes_only_the_proposal(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)
        runtime.approve(proposal.proposal_id, approver="pm")
        backend = _RecordingBackend()

        runtime.handoff(proposal.proposal_id, backend)

        args, kwargs = backend.calls[0]
        assert len(args) == 1
        assert kwargs == {}
        assert isinstance(args[0], PMProposal)

    def test_handoff_is_backend_agnostic(self, runtime: PMRuntime) -> None:
        proposal = runtime.propose(REQUEST)
        runtime.approve(proposal.proposal_id, approver="pm")

        class _AlternateBackend:
            def __init__(self) -> None:
                self.seen: list[object] = []

            def __call__(self, received):
                self.seen.append(received)
                return "alternate"

        alternate = _AlternateBackend()

        assert runtime.handoff(proposal.proposal_id, _RecordingBackend()) == (
            "backend-output"
        )
        assert runtime.handoff(proposal.proposal_id, alternate) == "alternate"
        assert alternate.seen == [runtime.get(proposal.proposal_id)]


# ------------------------------------------------------------------
# PM state authority
# ------------------------------------------------------------------

class TestPMStateAuthority:
    """PM owns project/task state; the runtime never transitions it."""

    def test_runtime_exposes_no_lifecycle_transition_api(self) -> None:
        for name in (
            "transition",
            "set_state",
            "apply_decision",
            "complete_task",
            "update_register",
            "interpret",
            "decide",
            "reconcile",
        ):
            assert not hasattr(PMRuntime, name), name

    def test_propose_approve_handoff_never_rewrite_pm_registers(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        watched = (
            root / "project" / "PROJECT_STATE.md",
            root / "project" / "TASKS.md",
            root / "MASTER_PLAN.md",
        )
        before = {path: path.read_bytes() for path in watched}

        proposal = runtime.propose(REQUEST)
        runtime.approve(proposal.proposal_id, approver="pm")
        backend = _RecordingBackend()
        runtime.handoff(proposal.proposal_id, backend)

        assert {path: path.read_bytes() for path in watched} == before
        # Only the proposal store was added under project/.
        assert sorted(entry.name for entry in (root / "project").iterdir()) == [
            "PROJECT_STATE.md",
            "TASKS.md",
            "proposals",
        ]

    def test_handoff_leaves_the_proposed_task_pending(
        self, runtime: PMRuntime
    ) -> None:
        proposal = runtime.propose(REQUEST)
        runtime.approve(proposal.proposal_id, approver="pm")
        runtime.handoff(proposal.proposal_id, _RecordingBackend())

        stored = runtime.get(proposal.proposal_id)
        assert stored.task.state is TaskState.PENDING
        assert runtime.context.tasks[1].state is TaskState.IMPLEMENTING


# ------------------------------------------------------------------
# Executable entry point: python -m pm
# ------------------------------------------------------------------

class TestCLIEntryPoint:
    """``python -m pm`` is the executable PM runtime entry point."""

    def test_propose_show_approve_round_trip(self, root: Path) -> None:
        proposed = _run_cli("propose", REQUEST, root=root)
        assert proposed.returncode == 0, proposed.stderr
        record = json.loads(proposed.stdout)
        assert record["proposal_id"].startswith("PROP-")
        assert record["request"] == REQUEST
        assert record["approved"] is False
        assert (root / "project" / "proposals").is_dir()

        shown = _run_cli("show", record["proposal_id"], root=root)
        assert shown.returncode == 0, shown.stderr
        assert json.loads(shown.stdout) == record

        approved = _run_cli(
            "approve", record["proposal_id"], "--approver", "pm", root=root
        )
        assert approved.returncode == 0, approved.stderr
        approved_record = json.loads(approved.stdout)
        assert approved_record["approved"] is True
        assert approved_record["approval"]["approver"] == "pm"

        # The runtime agrees with what the CLI printed.
        assert PMRuntime(root=root).get(record["proposal_id"]).is_approved

    def test_blank_request_fails_closed(self, root: Path) -> None:
        result = _run_cli("propose", "   ", root=root)

        assert result.returncode == 1
        assert result.stdout.strip() == ""
        assert result.stderr.startswith("pm: error:")
        assert not (root / "project" / "proposals").exists()

    def test_unknown_proposal_fails_closed(self, root: Path) -> None:
        result = _run_cli("show", UNKNOWN_VALID_ID, root=root)

        assert result.returncode == 1
        assert result.stdout.strip() == ""
        assert "no proposal exists" in result.stderr

    def test_a_subcommand_is_required(self, root: Path) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pm"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 2
        assert result.stdout.strip() == ""


if __name__ == "__main__":
    pytest.main([__file__])
