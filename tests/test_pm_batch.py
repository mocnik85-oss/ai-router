"""MVP-002 focused tests — batch representation, batch approval, gate.

Evidence produced here (offline only — no network, model, provider, or
real backend call):

- a batch is a machine-readable record that *contains* the proposals
  (and their ``PENDING`` V1.1 tasks) it authorizes, and keeps the
  traceability chain to project identity, project version, and
  requirement version;
- batch identity is deterministic and idempotent: the same member set
  in the same context derives the same ``BATCH-<16 hex>`` identity in
  any member order, re-proposing never forks a second record and never
  drops an approval, and a different membership is a different batch;
- the invalid paths fail closed before anything is written: empty,
  repeated, malformed, or unknown membership, mixed projects or
  requirement versions, relabelled identities, and tampered records;
- the unapproved path never reaches a backend: neither
  ``handoff_batch`` nor the handoff of a proposal that belongs to an
  unapproved batch is dispatched, and batch approval never substitutes
  for a missing member approval;
- the approved path works: one explicit PM batch approval is recorded,
  is immutable, changes nothing but the approval, and then authorizes
  the gated batch handoff (and member handoffs);
- PM stays the authoritative owner: batch operations rewrite no
  PM-owned register, expose no lifecycle-transition API, and the batch
  model carries no backend, policy, credential, or permission field —
  no authoritative decision is delegated to an implementation backend.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from pm import PMBatch, PMProposal, PMRuntime, ProjectContext
from pm.batch import (
    BATCH_ID_RE,
    BATCH_SCHEMA,
    BATCH_SCHEMA_VERSION,
    derive_batch_identity,
    normalize_member_ids,
)
from pm.errors import (
    BatchAlreadyApprovedError,
    BatchNotApprovedError,
    BatchNotFoundError,
    PMRuntimeError,
    ProposalNotFoundError,
    ProposalNotApprovedError,
)
from pm.proposal import ApprovalRecord
from protocol.artifacts import (
    ArtifactValidationError,
    ProjectArtifact,
    TaskArtifact,
    TaskState,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

REQUEST_ONE = "Add a --version flag to the PM CLI."
REQUEST_TWO = "Document the PM proposal flow in the README."
REQUEST_THREE = "Record batch approvals in the PM runtime."

PROJECT_ID = "AI Router / JEV"
PROJECT_VERSION = "v1.1-migration"
PROTOCOL_VERSION = "V1.1"

#: An identity that looks well-formed but addresses no stored record.
UNKNOWN_VALID_BATCH_ID = "BATCH-0000000000000000"

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
    prove the backend was never reached when it should not have been.
    """

    def __init__(self, value: object = "backend-output") -> None:
        self.value = value
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.value


def _project() -> ProjectArtifact:
    return ProjectArtifact(
        project_id=PROJECT_ID, project_version=PROJECT_VERSION
    )


def _batches(root: Path) -> Path:
    return root / "project" / "batches"


def _batch_path(root: Path, batch: PMBatch) -> Path:
    return _batches(root) / f"{batch.batch_id}.json"


def _proposal_path(root: Path, proposal: PMProposal) -> Path:
    return root / "project" / "proposals" / f"{proposal.proposal_id}.json"


def _propose_two(runtime: PMRuntime) -> tuple[PMProposal, PMProposal]:
    return runtime.propose(REQUEST_ONE), runtime.propose(REQUEST_TWO)


def _run_cli(*args: str, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pm", *args, "--root", str(root)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ------------------------------------------------------------------
# Normal batch path
# ------------------------------------------------------------------

class TestBatchProposal:
    """A batch contains the proposals it authorizes, traceably."""

    def test_propose_batch_contains_the_proposals_it_authorizes(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)

        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )

        assert isinstance(batch, PMBatch)
        # Identity and V1.1 vocabulary.
        assert BATCH_ID_RE.match(batch.batch_id)
        digest = batch.batch_id.split("-", 1)[1]
        assert batch.correlation_id == f"CORR-BATCH-{digest}"
        assert batch.idempotency_key
        # Traceability: project identity/version + requirement version.
        assert batch.project == first.project == second.project
        assert batch.project.project_id == PROJECT_ID
        assert batch.project.project_version == PROJECT_VERSION
        assert batch.requirement_version == PROTOCOL_VERSION
        # The batch contains exactly the proposals (and PENDING tasks).
        assert batch.proposals == tuple(
            sorted((first, second), key=lambda p: p.proposal_id)
        )
        assert set(batch.proposal_ids) == {
            first.proposal_id,
            second.proposal_id,
        }
        for member in batch.proposals:
            assert isinstance(member, PMProposal)
            assert isinstance(member.task, TaskArtifact)
            assert member.task.state is TaskState.PENDING
            assert member.task.requirement_version == batch.requirement_version
            assert member.project == batch.project
        # Unapproved: the absence of the record is the refused state.
        assert batch.approval is None
        assert batch.is_approved is False

    def test_batch_record_is_machine_readable_and_round_trips(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        path = _batch_path(root, batch)

        assert path.is_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["schema"] == BATCH_SCHEMA
        assert data["schema_version"] == BATCH_SCHEMA_VERSION
        assert data["batch_id"] == batch.batch_id
        assert data["approved"] is False
        assert data["approval"] is None
        assert data["project"]["project_id"] == PROJECT_ID
        assert data["project"]["project_version"] == PROJECT_VERSION
        assert data["requirement_version"] == PROTOCOL_VERSION
        assert isinstance(data["proposals"], list)
        assert len(data["proposals"]) == 2
        # Members travel through the existing MVP-001 proposal model.
        for member in data["proposals"]:
            assert member["schema"].startswith("ai-router.pm.proposal")
            assert member["task"]["state"] == "PENDING"

        assert PMBatch.from_json(path.read_text(encoding="utf-8")) == batch

    def test_batch_identity_is_deterministic_and_order_independent(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        forward = derive_batch_identity(
            _project(),
            PROTOCOL_VERSION,
            [first.proposal_id, second.proposal_id],
        )
        reversed_ = derive_batch_identity(
            _project(),
            PROTOCOL_VERSION,
            [second.proposal_id, first.proposal_id],
        )

        assert forward == reversed_
        assert BATCH_ID_RE.match(forward.batch_id)

        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        assert batch.batch_id == forward.batch_id
        assert batch.correlation_id == forward.correlation_id
        assert batch.idempotency_key == forward.idempotency_key

        # A different context derives a different identity.
        other = derive_batch_identity(
            ProjectArtifact(project_id="other", project_version="v9"),
            PROTOCOL_VERSION,
            [first.proposal_id, second.proposal_id],
        )
        assert other.batch_id != forward.batch_id
        other_requirement = derive_batch_identity(
            _project(), "V2.0", [first.proposal_id, second.proposal_id]
        )
        assert other_requirement.batch_id != forward.batch_id

    def test_propose_batch_is_idempotent_and_preserves_approval(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        approved = runtime.approve_batch(batch.batch_id, approver="pm")

        again = runtime.propose_batch(
            [second.proposal_id, first.proposal_id]  # reordered
        )

        assert again.batch_id == batch.batch_id
        assert again.is_approved
        assert again.approval == approved.approval
        assert len(list(_batches(root).glob("*.json"))) == 1
        # The stored record agrees with the returned record.
        assert runtime.get_batch(batch.batch_id) == again

    def test_a_different_membership_is_a_different_batch(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        third = runtime.propose(REQUEST_THREE)

        pair = runtime.propose_batch([first.proposal_id, second.proposal_id])
        triple = runtime.propose_batch(
            [first.proposal_id, second.proposal_id, third.proposal_id]
        )
        single = runtime.propose_batch([first.proposal_id])

        ids = {pair.batch_id, triple.batch_id, single.batch_id}
        assert len(ids) == 3
        assert triple.proposal_ids == tuple(
            sorted(
                {
                    first.proposal_id,
                    second.proposal_id,
                    third.proposal_id,
                }
            )
        )

    def test_invalid_membership_is_refused_before_anything_is_written(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)

        # Empty, blank, malformed, and repeated membership.
        with pytest.raises(ArtifactValidationError, match="at least one"):
            runtime.propose_batch([])
        with pytest.raises(ArtifactValidationError, match="sequence"):
            runtime.propose_batch(first.proposal_id)  # type: ignore[arg-type]
        with pytest.raises(ArtifactValidationError, match="not a proposal"):
            runtime.propose_batch(["PROP-ZZZZZZZZZZZZZZZZ"])
        with pytest.raises(ArtifactValidationError, match="not a proposal"):
            runtime.propose_batch(["../../etc/passwd"])
        with pytest.raises(ArtifactValidationError, match="not a proposal"):
            runtime.propose_batch([42])  # type: ignore[list-item]
        with pytest.raises(
            ArtifactValidationError, match="must not repeat"
        ):
            runtime.propose_batch(
                [first.proposal_id, first.proposal_id]
            )
        # A well-formed identity that addresses no stored proposal.
        with pytest.raises(ProposalNotFoundError):
            runtime.propose_batch(["PROP-0000000000000000"])

        # Nothing at all was written for any of the refusals.
        assert not _batches(root).exists()
        assert sorted(
            p.name for p in (root / "project" / "proposals").glob("*.json")
        ) == sorted(
            [first.proposal_id + ".json", second.proposal_id + ".json"]
        )

    def test_normalization_rejects_bare_and_repeated_membership(self) -> None:
        with pytest.raises(ArtifactValidationError, match="sequence"):
            normalize_member_ids("PROP-0000000000000000")
        with pytest.raises(ArtifactValidationError, match="sequence"):
            derive_batch_identity(
                _project(), PROTOCOL_VERSION, "PROP-0000000000000000"
            )  # type: ignore[arg-type]
        with pytest.raises(
            ArtifactValidationError, match="must not repeat"
        ):
            normalize_member_ids(
                ["PROP-1111111111111111", "PROP-1111111111111111"]
            )
        # Canonical order is sorted and stable.
        assert normalize_member_ids(
            ["PROP-2222222222222222", "PROP-1111111111111111"]
        ) == ("PROP-1111111111111111", "PROP-2222222222222222")


# ------------------------------------------------------------------
# Internal consistency: invalid batches never load or approve
# ------------------------------------------------------------------

class TestBatchConsistency:
    """A batch that is internally inconsistent is refused, fail-closed."""

    def test_a_batch_cannot_mix_projects_or_project_versions(
        self, runtime: PMRuntime
    ) -> None:
        first, _ = _propose_two(runtime)
        foreign = ProjectArtifact(project_id="other", project_version="v9")
        identity = derive_batch_identity(
            foreign, PROTOCOL_VERSION, [first.proposal_id]
        )

        with pytest.raises(ArtifactValidationError, match="project"):
            PMBatch(
                batch_id=identity.batch_id,
                correlation_id=identity.correlation_id,
                idempotency_key=identity.idempotency_key,
                created_at="2026-01-01T00:00:00+00:00",
                project=foreign,
                requirement_version=PROTOCOL_VERSION,
                proposals=(first,),
            )

    def test_a_batch_cannot_mix_requirement_versions(
        self, runtime: PMRuntime
    ) -> None:
        first, _ = _propose_two(runtime)
        identity = derive_batch_identity(
            _project(), "V2.0", [first.proposal_id]
        )

        with pytest.raises(
            ArtifactValidationError, match="requirement version"
        ):
            PMBatch(
                batch_id=identity.batch_id,
                correlation_id=identity.correlation_id,
                idempotency_key=identity.idempotency_key,
                created_at="2026-01-01T00:00:00+00:00",
                project=_project(),
                requirement_version="V2.0",
                proposals=(first,),
            )

    def test_a_batch_identity_cannot_be_relabelled(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )

        with pytest.raises(
            ArtifactValidationError, match="does not match"
        ):
            replace(batch, batch_id=UNKNOWN_VALID_BATCH_ID)
        with pytest.raises(
            ArtifactValidationError, match="does not match"
        ):
            replace(batch, idempotency_key="batch:something:else")

    def test_a_batch_refuses_repeated_empty_and_foreign_members(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch([first.proposal_id])
        base = dict(
            batch_id=batch.batch_id,
            correlation_id=batch.correlation_id,
            idempotency_key=batch.idempotency_key,
            created_at=batch.created_at,
            project=batch.project,
            requirement_version=batch.requirement_version,
        )

        with pytest.raises(
            ArtifactValidationError, match="must not repeat"
        ):
            PMBatch(proposals=(first, first), **base)
        with pytest.raises(ArtifactValidationError, match="at least one"):
            PMBatch(proposals=(), **base)
        with pytest.raises(
            ArtifactValidationError, match="PM proposal records"
        ):
            PMBatch(proposals=(first, "PROP-0000000000000000"), **base)  # type: ignore[arg-type]
        # The identity of a single-member batch is not the identity of
        # the repeated/foreign variants, so those constructions could
        # never be persisted under a valid identity either.

    def test_batch_record_fails_closed_on_tampering(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        record = batch.to_dict()

        wrong_schema = dict(record, schema="ai-router.pm.other")
        with pytest.raises(ArtifactValidationError, match="batch schema"):
            PMBatch.from_dict(wrong_schema)

        wrong_version = dict(record, schema_version="2")
        with pytest.raises(
            ArtifactValidationError, match="schema version"
        ):
            PMBatch.from_dict(wrong_version)

        flag_disagrees = dict(record, approved=True, approval=None)
        with pytest.raises(ArtifactValidationError, match="disagree"):
            PMBatch.from_dict(flag_disagrees)

        with pytest.raises(ArtifactValidationError, match="batch record"):
            PMBatch.from_dict("not an object")

        with pytest.raises(
            ArtifactValidationError, match="must be a list"
        ):
            PMBatch.from_dict(dict(record, proposals="PROP-1"))

        with pytest.raises(ArtifactValidationError, match="not valid JSON"):
            PMBatch.from_json("{not json")

        # A member smuggled in from another requirement version (the
        # member record is internally consistent, but foreign to this
        # batch).
        first_record = first.to_dict()
        foreign_member = dict(
            first_record,
            requirement_version="V9.9",
            task=dict(first_record["task"], requirement_version="V9.9"),
        )
        tampered = dict(
            record, proposals=[foreign_member, second.to_dict()]
        )
        with pytest.raises(
            ArtifactValidationError, match="requirement version"
        ):
            PMBatch.from_dict(tampered)

        # A duplicated member is refused on load too.
        duplicated = dict(
            record, proposals=[first.to_dict(), first.to_dict()]
        )
        with pytest.raises(
            ArtifactValidationError, match="must not repeat"
        ):
            PMBatch.from_dict(duplicated)

    def test_a_missing_member_blocks_approval_and_handoff(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        backend = _RecordingBackend()

        _proposal_path(root, second).unlink()

        with pytest.raises(ProposalNotFoundError):
            runtime.approve_batch(batch.batch_id, approver="pm")
        # The stored batch is still unapproved, so handoff stays shut.
        with pytest.raises(BatchNotApprovedError):
            runtime.handoff_batch(batch.batch_id, backend)
        assert runtime.get_batch(batch.batch_id).approval is None
        assert backend.calls == []

    def test_a_drifted_member_blocks_approval_and_handoff(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        backend = _RecordingBackend()

        # Tamper with one stored member after the batch was proposed.
        path = _proposal_path(root, first)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["summary"] = "silently rewritten summary"
        path.write_text(json.dumps(data), encoding="utf-8")

        # The inconsistent batch can never be approved…
        with pytest.raises(PMRuntimeError, match="no longer matches"):
            runtime.approve_batch(batch.batch_id, approver="pm")
        assert runtime.get_batch(batch.batch_id).approval is None

        # …and an already-approved batch refuses the drift before the
        # backend is reached.
        third = runtime.propose(REQUEST_THREE)
        clean = runtime.propose_batch([third.proposal_id])
        runtime.approve(third.proposal_id, approver="pm")
        runtime.approve_batch(clean.batch_id, approver="pm")

        third_path = _proposal_path(root, third)
        tampered = json.loads(third_path.read_text(encoding="utf-8"))
        tampered["summary"] = "another rewritten summary"
        third_path.write_text(json.dumps(tampered), encoding="utf-8")

        with pytest.raises(PMRuntimeError, match="no longer matches"):
            runtime.handoff_batch(clean.batch_id, backend)
        assert backend.calls == []

    def test_batch_carries_no_backend_or_permission_fields(self) -> None:
        fields = set(PMBatch.__dataclass_fields__)
        assert fields == {
            "batch_id",
            "correlation_id",
            "idempotency_key",
            "created_at",
            "project",
            "requirement_version",
            "proposals",
            "approval",
        }
        assert not fields & {
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
            "state",
            "decision",
        }
        # Approval reuse: the same explicit PM-side record as MVP-001.
        assert ApprovalRecord.__dataclass_fields__.keys() == {
            "approver",
            "approved_at",
            "note",
        }


# ------------------------------------------------------------------
# Unapproved batches: rejection / prevention of any backend reach
# ------------------------------------------------------------------

class TestUnapprovedBatchGate:
    """No execution or handoff occurs before batch approval."""

    def test_an_unapproved_batch_never_reaches_the_backend(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        backend = _RecordingBackend()

        # The batch exists on disk, yet the handoff is still refused.
        assert _batch_path(root, batch).is_file()
        with pytest.raises(BatchNotApprovedError, match="not approved"):
            runtime.handoff_batch(batch.batch_id, backend)

        assert backend.calls == []
        assert runtime.get_batch(batch.batch_id).approval is None

    def test_a_member_of_an_unapproved_batch_never_reaches_the_backend(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        # Even an individually approved member stays shut until the
        # batch that authorizes it is approved.
        runtime.approve(first.proposal_id, approver="pm")
        backend = _RecordingBackend()

        with pytest.raises(BatchNotApprovedError, match=batch.batch_id):
            runtime.handoff(first.proposal_id, backend)

        assert backend.calls == []

    def test_a_proposal_outside_the_batch_keeps_the_proposal_gate(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        runtime.propose_batch([first.proposal_id])
        runtime.approve(second.proposal_id, approver="pm")
        backend = _RecordingBackend()

        # The second proposal belongs to no batch: MVP-001 semantics.
        assert runtime.handoff(second.proposal_id, backend) == (
            "backend-output"
        )
        assert len(backend.calls) == 1

        # …while the batched first proposal still fails closed.
        runtime.approve(first.proposal_id, approver="pm")
        with pytest.raises(BatchNotApprovedError):
            runtime.handoff(first.proposal_id, backend)
        assert len(backend.calls) == 1

    def test_batch_handoff_refuses_an_unapproved_member(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        runtime.approve_batch(batch.batch_id, approver="pm")
        runtime.approve(first.proposal_id, approver="pm")
        backend = _RecordingBackend()

        # Batch approved, but one member has no approval of its own:
        # batch approval never substitutes for a member approval.
        with pytest.raises(
            ProposalNotApprovedError, match=second.proposal_id
        ):
            runtime.handoff_batch(batch.batch_id, backend)
        with pytest.raises(ProposalNotApprovedError):
            runtime.handoff(second.proposal_id, backend)

        assert backend.calls == []
        assert runtime.get(second.proposal_id).is_approved is False

    def test_unknown_batch_identities_fail_closed(
        self, runtime: PMRuntime
    ) -> None:
        backend = _RecordingBackend()

        for identity in (
            UNKNOWN_VALID_BATCH_ID,
            "not-a-batch",
            "../PROJECT_STATE",
            "batch-0000000000000000",
            "",
        ):
            with pytest.raises(BatchNotFoundError, match="no batch exists"):
                runtime.get_batch(identity)
            with pytest.raises(BatchNotFoundError, match="no batch exists"):
                runtime.approve_batch(identity, approver="pm")
            with pytest.raises(BatchNotFoundError, match="no batch exists"):
                runtime.handoff_batch(identity, backend)

        assert backend.calls == []

    def test_handoff_batch_requires_a_callable_backend(
        self, runtime: PMRuntime
    ) -> None:
        first, _ = _propose_two(runtime)
        batch = runtime.propose_batch([first.proposal_id])

        with pytest.raises(PMRuntimeError, match="callable"):
            runtime.handoff_batch(batch.batch_id, object())  # type: ignore[arg-type]

    def test_an_unreadable_batch_record_blocks_member_handoff(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, _ = _propose_two(runtime)
        runtime.propose_batch([first.proposal_id])
        runtime.approve(first.proposal_id, approver="pm")
        # A corrupted record at a well-formed batch identity must not
        # hide proposals from the membership scan: the scan fails
        # closed instead of skipping it.
        corrupted = _batches(root) / "BATCH-FFFFFFFFFFFFFFFF.json"
        corrupted.write_text("{not json", encoding="utf-8")
        backend = _RecordingBackend()

        with pytest.raises(PMRuntimeError, match="invalid"):
            runtime.handoff(first.proposal_id, backend)

        assert backend.calls == []


# ------------------------------------------------------------------
# Approved batch: the explicit PM act and the authorized handoff
# ------------------------------------------------------------------

class TestApprovedBatch:
    """One explicit, immutable PM batch approval unlocks the gate."""

    def test_approve_batch_records_the_explicit_pm_approval(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )

        approved = runtime.approve_batch(
            batch.batch_id, approver="pm", note="mvp-002"
        )

        assert approved.is_approved
        assert isinstance(approved.approval, ApprovalRecord)
        assert approved.approval.approver == "pm"
        assert approved.approval.approved_at
        assert approved.approval.note == "mvp-002"
        assert approved.batch_id == batch.batch_id
        assert approved.proposals == batch.proposals
        # Persisted and reloadable.
        on_disk = json.loads(_batch_path(root, approved).read_text(encoding="utf-8"))
        assert on_disk["approved"] is True
        assert on_disk["approval"]["approver"] == "pm"
        assert runtime.get_batch(batch.batch_id) == approved

    def test_a_batch_approval_cannot_be_overwritten(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        first, _ = _propose_two(runtime)
        batch = runtime.propose_batch([first.proposal_id])
        first_approval = runtime.approve_batch(
            batch.batch_id, approver="pm-one"
        )

        with pytest.raises(BatchAlreadyApprovedError):
            runtime.approve_batch(batch.batch_id, approver="pm-two")

        stored = runtime.get_batch(batch.batch_id)
        assert stored.approval == first_approval.approval
        assert stored.approval.approver == "pm-one"
        on_disk = json.loads(_batch_path(root, stored).read_text(encoding="utf-8"))
        assert on_disk["approval"]["approver"] == "pm-one"

    def test_approve_batch_rejects_a_blank_approver(
        self, runtime: PMRuntime
    ) -> None:
        first, _ = _propose_two(runtime)
        batch = runtime.propose_batch([first.proposal_id])

        for blank in ("", "   "):
            with pytest.raises(ArtifactValidationError, match="approver"):
                runtime.approve_batch(batch.batch_id, approver=blank)
        with pytest.raises(ArtifactValidationError, match="note"):
            runtime.approve_batch(
                batch.batch_id, approver="pm", note=42  # type: ignore[arg-type]
            )
        assert runtime.get_batch(batch.batch_id).approval is None

    def test_batch_approval_changes_nothing_but_the_approval(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )

        approved = runtime.approve_batch(batch.batch_id, approver="pm")

        before, after = batch.to_dict(), approved.to_dict()
        assert set(before) == set(after)
        changed = {key for key in before if before[key] != after[key]}
        assert changed == {"approval", "approved"}
        assert approved.proposals == batch.proposals
        # Members' own approval state is untouched by batch approval.
        assert runtime.get(first.proposal_id).is_approved is False
        assert runtime.get(second.proposal_id).is_approved is False

    def test_an_approved_batch_hands_the_batch_to_the_backend(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        runtime.approve(first.proposal_id, approver="pm")
        runtime.approve(second.proposal_id, approver="pm")
        runtime.approve_batch(batch.batch_id, approver="pm")
        backend = _RecordingBackend(value="raw batch output")

        outcome = runtime.handoff_batch(batch.batch_id, backend)

        assert outcome == "raw batch output"
        assert len(backend.calls) == 1
        args, kwargs = backend.calls[0]
        assert len(args) == 1 and kwargs == {}
        handed_over = args[0]
        assert isinstance(handed_over, PMBatch)
        assert handed_over.batch_id == batch.batch_id
        assert handed_over.is_approved
        assert handed_over.proposal_ids == batch.proposal_ids
        # Handing off changes no lifecycle state anywhere.
        for member in handed_over.proposals:
            assert member.task.state is TaskState.PENDING

    def test_batch_approval_unblocks_member_handoff(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        runtime.approve(first.proposal_id, approver="pm")
        runtime.approve(second.proposal_id, approver="pm")
        runtime.approve_batch(batch.batch_id, approver="pm")
        backend = _RecordingBackend()

        assert runtime.handoff(first.proposal_id, backend) == "backend-output"
        assert runtime.handoff(second.proposal_id, backend) == "backend-output"
        assert len(backend.calls) == 2
        for args, _ in backend.calls:
            assert isinstance(args[0], PMProposal)
            assert args[0].is_approved

    def test_an_unknown_batch_identity_never_reaches_the_backend(
        self, runtime: PMRuntime
    ) -> None:
        first, second = _propose_two(runtime)
        runtime.approve(first.proposal_id, approver="pm")
        runtime.approve(second.proposal_id, approver="pm")
        backend = _RecordingBackend()

        with pytest.raises(BatchNotFoundError):
            runtime.handoff_batch(UNKNOWN_VALID_BATCH_ID, backend)

        assert backend.calls == []


# ------------------------------------------------------------------
# PM authority and scope
# ------------------------------------------------------------------

class TestPMAuthorityAndScope:
    """The batch mechanism decides nothing for a backend and rewrites
    no PM-owned record."""

    def test_batch_flow_rewrites_no_pm_register(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        watched = (
            root / "project" / "PROJECT_STATE.md",
            root / "project" / "TASKS.md",
            root / "MASTER_PLAN.md",
        )
        before = {path: path.read_bytes() for path in watched}

        first, second = _propose_two(runtime)
        batch = runtime.propose_batch(
            [first.proposal_id, second.proposal_id]
        )
        runtime.approve(first.proposal_id, approver="pm")
        runtime.approve(second.proposal_id, approver="pm")
        runtime.approve_batch(batch.batch_id, approver="pm")
        runtime.handoff_batch(batch.batch_id, _RecordingBackend())

        assert {path: path.read_bytes() for path in watched} == before
        # Only the two PM-owned record directories exist under project/.
        assert sorted(entry.name for entry in (root / "project").iterdir()) == [
            "PROJECT_STATE.md",
            "TASKS.md",
            "batches",
            "proposals",
        ]

    def test_runtime_exposes_no_batch_lifecycle_authority(self) -> None:
        for name in (
            "transition",
            "set_state",
            "apply_decision",
            "complete_task",
            "update_register",
            "interpret",
            "decide",
            "reconcile",
            "schedule",
            "dispatch",
        ):
            assert not hasattr(PMRuntime, name), name
            assert not hasattr(PMBatch, name), name

    def test_the_context_register_is_untouched_by_batches(
        self, runtime: PMRuntime, root: Path
    ) -> None:
        before = ProjectContext.load(root)

        first, _ = _propose_two(runtime)
        runtime.propose_batch([first.proposal_id])

        after = ProjectContext.load(root)
        assert after == before
        assert after.tasks[1].state is TaskState.IMPLEMENTING


# ------------------------------------------------------------------
# Executable entry point: python -m pm (batch commands)
# ------------------------------------------------------------------

class TestBatchCLIEntryPoint:
    """``python -m pm`` exposes the batch proposal and approval path."""

    def test_propose_show_approve_batch_round_trip(self, root: Path) -> None:
        ids = []
        for request in (REQUEST_ONE, REQUEST_TWO):
            proposed = _run_cli("propose", request, root=root)
            assert proposed.returncode == 0, proposed.stderr
            ids.append(json.loads(proposed.stdout)["proposal_id"])

        created = _run_cli("propose-batch", *ids, root=root)
        assert created.returncode == 0, created.stderr
        record = json.loads(created.stdout)
        assert record["batch_id"].startswith("BATCH-")
        assert record["approved"] is False
        assert len(record["proposals"]) == 2

        shown = _run_cli("show-batch", record["batch_id"], root=root)
        assert shown.returncode == 0, shown.stderr
        assert json.loads(shown.stdout) == record

        approved = _run_cli(
            "approve-batch",
            record["batch_id"],
            "--approver",
            "pm",
            root=root,
        )
        assert approved.returncode == 0, approved.stderr
        approved_record = json.loads(approved.stdout)
        assert approved_record["approved"] is True
        assert approved_record["approval"]["approver"] == "pm"

        # The runtime agrees with what the CLI printed.
        assert PMRuntime(root=root).get_batch(record["batch_id"]).is_approved

    def test_unknown_batch_identity_fails_closed(self, root: Path) -> None:
        result = _run_cli("show-batch", UNKNOWN_VALID_BATCH_ID, root=root)

        assert result.returncode == 1
        assert result.stdout.strip() == ""
        assert "no batch exists" in result.stderr
        assert not (root / "project" / "batches").exists()

    def test_unknown_member_identity_fails_closed(self, root: Path) -> None:
        result = _run_cli(
            "propose-batch", "PROP-0000000000000000", root=root
        )

        assert result.returncode == 1
        assert result.stdout.strip() == ""
        assert "no proposal exists" in result.stderr
        assert not (root / "project" / "batches").exists()


if __name__ == "__main__":
    pytest.main([__file__])
