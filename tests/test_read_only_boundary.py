"""TASK-004 focused tests — read-only enforcement at the execution boundary.

These tests prove that the V1.1 read-only execution policy is enforced
*inside the provider boundary* — ``providers.opencode.OpenCodeClient.run``,
immediately before the concrete OpenCode process would be spawned — and
not merely by JEV's upstream ``_validate_policy`` check.

Evidence produced here:

- a permitted read-only execution still reaches the provider and succeeds;
- a prohibited write-capable operation never spawns the provider process;
- the governing policy object is transported from the governed task to
  the provider/client boundary unchanged;
- enforcement survives even when the upstream validation layer is
  bypassed, which an upstream-only check could not survive;
- repository state can be verified before and after execution.

No real OpenCode process, network call, or paid model is used: the
subprocess layer is monkeypatched throughout.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from providers.opencode import OpenCodeClient, OpenCodePolicyError
from providers.openrouter import OpenRouterModel
from router.jev import JEV, JEVTask, PolicyViolationError
from router.policy import READ_ONLY, ExecutionPolicy


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

class _RecordingOpenCodeClient(OpenCodeClient):
    """Real boundary client that records each governed execution request.

    Every call is delegated to :meth:`OpenCodeClient.run`, so each
    recorded request still passes through the actual enforcement point.
    """

    def __init__(self) -> None:
        super().__init__()
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
        return super().run(prompt, workdir, model=model, policy=policy)


class _FakeCompleted:
    """Canned ``subprocess.run`` result standing in for the OpenCode CLI."""

    def __init__(self) -> None:
        self.returncode = 0
        self.stdout = '{"type":"text","part":{"text":"ok"}}\n'
        self.stderr = ""


def _install_fake_subprocess(monkeypatch) -> list[dict]:
    """Replace the provider's subprocess with a recorder.

    Returns the list of spawn attempts; an empty list proves the
    prohibited execution never reached the operating system.
    """

    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return _FakeCompleted()

    monkeypatch.setattr("providers.opencode.subprocess.run", fake_run)
    return calls


def _free_model(model_id: str = "opencode/task004-free") -> OpenRouterModel:
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


def _snapshot(root: Path) -> dict[str, tuple]:
    """Serializable before/after snapshot of a workspace/repository."""

    entries: dict[str, tuple] = {}

    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))

        if path.is_dir():
            entries[rel] = ("dir",)
        elif path.is_file():
            data = path.read_bytes()
            entries[rel] = (
                "file",
                len(data),
                hashlib.sha256(data).hexdigest(),
                path.stat().st_mtime_ns,
            )

    return entries


@pytest.fixture
def repo(tmp_path) -> Path:
    """A small repository-shaped workspace, including git metadata."""

    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/master\n")
    (workspace / "app.py").write_text("print('hi')\n")
    return workspace


# ------------------------------------------------------------------
# 1. Permitted read-only execution still works
# ------------------------------------------------------------------


class TestPermittedReadOnlyExecution:
    def test_read_only_execution_succeeds_at_the_boundary(
        self, monkeypatch, tmp_path
    ):
        """A READ_ONLY task reaches the provider boundary and executes."""

        calls = _install_fake_subprocess(monkeypatch)
        client = _RecordingOpenCodeClient()
        jev = JEV(client=client)
        model = _free_model()

        task = JEVTask(
            prompt="inspect the repository",
            workdir=str(tmp_path),
            policy=READ_ONLY,
        )
        result = jev.run(task, available_models=[model])

        assert result.success
        assert result.model == "opencode/task004-free"

        # The concrete provider process was spawned exactly once, with
        # the same command shape used before TASK-004 (no new flags),
        # and never with permission auto-approval.
        assert len(calls) == 1
        assert calls[0]["command"] == [
            client.executable,
            "run",
            "--format",
            "json",
            "--model",
            "opencode/task004-free",
            "inspect the repository",
        ]
        assert "--auto" not in calls[0]["command"]

    def test_task_policy_object_reaches_the_boundary_unchanged(
        self, monkeypatch, tmp_path
    ):
        """The exact policy object of the governed task is transmitted
        to the provider/client boundary — not stripped, not replaced."""

        _install_fake_subprocess(monkeypatch)
        client = _RecordingOpenCodeClient()
        jev = JEV(client=client)
        model = _free_model()
        policy = ExecutionPolicy(timeout=9.0)  # non-default, read-only

        task = JEVTask(
            prompt="read files", workdir=str(tmp_path), policy=policy
        )
        result = jev.run(task, available_models=[model])

        assert result.success
        assert len(client.requests) == 1
        assert client.requests[0]["policy"] is task.policy

    def test_direct_read_only_call_still_works(self, monkeypatch, tmp_path):
        """Calling the provider without an explicit policy remains
        permitted and is governed as READ_ONLY (fail-closed default)."""

        calls = _install_fake_subprocess(monkeypatch)

        result = OpenCodeClient().run("hello", tmp_path)

        assert result.success
        assert len(calls) == 1


# ------------------------------------------------------------------
# 2. Prohibited write-capable operations are blocked at the boundary
# ------------------------------------------------------------------


class TestProhibitedOperationBlockedAtBoundary:
    @pytest.mark.parametrize(
        "capability", ["edit", "shell", "network", "git", "commit"]
    )
    def test_write_capable_policy_is_refused_before_spawn(
        self, monkeypatch, tmp_path, capability
    ):
        """A write-capable policy handed directly to the provider (i.e.
        with JEV and its upstream check bypassed) is refused *inside*
        the boundary, before any OpenCode process is spawned."""

        calls = _install_fake_subprocess(monkeypatch)
        client = OpenCodeClient()
        policy = ExecutionPolicy(**{capability: True})

        with pytest.raises(OpenCodePolicyError) as excinfo:
            client.run("modify the file", tmp_path, policy=policy)

        assert capability in str(excinfo.value)
        assert "read-only execution boundary" in str(excinfo.value)
        assert calls == []  # the provider process was never spawned

    def test_unknown_policy_object_is_refused(self, monkeypatch, tmp_path):
        """An object that is not an ExecutionPolicy cannot prove
        read-only conformance, so the boundary fails closed."""

        calls = _install_fake_subprocess(monkeypatch)

        with pytest.raises(OpenCodePolicyError):
            OpenCodeClient().run("hello", tmp_path, policy="read-only")

        assert calls == []


# ------------------------------------------------------------------
# 3. Enforcement is at the boundary, not only upstream
# ------------------------------------------------------------------


class TestBoundaryEnforcementNotOnlyUpstream:
    def test_boundary_blocks_even_when_upstream_validation_is_bypassed(
        self, monkeypatch, tmp_path
    ):
        """With JEV's upstream ``_validate_policy`` deliberately
        disabled, a write-capable task still cannot execute: the
        provider boundary refuses it and no process is spawned."""

        calls = _install_fake_subprocess(monkeypatch)
        client = _RecordingOpenCodeClient()
        jev = JEV(client=client)
        model = _free_model()
        policy = ExecutionPolicy(edit=True, commit=True)

        # Simulate a regression/absence of the upstream validation layer.
        monkeypatch.setattr(
            JEV, "_validate_policy", staticmethod(lambda _policy: None)
        )

        task = JEVTask(
            prompt="edit the file and commit it",
            workdir=str(tmp_path),
            policy=policy,
        )
        result = jev.run(task, available_models=[model])

        # The policy did reach the boundary...
        assert client.requests
        assert client.requests[0]["policy"] is task.policy
        # ...was enforced there...
        assert result.success is False
        assert "read-only execution boundary" in (result.error or "")
        # ...and the prohibited execution never started.
        assert calls == []

    def test_upstream_validation_still_rejects_write_policies(self, tmp_path):
        """Existing compatible behavior: JEV's upstream check still
        raises PolicyViolationError before any model work happens."""

        jev = JEV()
        task = JEVTask(
            prompt="modify things",
            workdir=str(tmp_path),
            policy=ExecutionPolicy(edit=True),
        )

        with pytest.raises(PolicyViolationError):
            jev.run(task)


# ------------------------------------------------------------------
# 4. Repository state can be verified before and after execution
# ------------------------------------------------------------------


class TestRepositoryStateAroundExecution:
    def test_prohibited_execution_leaves_repository_unchanged(
        self, monkeypatch, repo
    ):
        _install_fake_subprocess(monkeypatch)
        before = _snapshot(repo)

        with pytest.raises(OpenCodePolicyError):
            OpenCodeClient().run(
                "commit everything",
                repo,
                policy=ExecutionPolicy(git=True, commit=True),
            )

        assert _snapshot(repo) == before

    def test_permitted_execution_leaves_repository_unchanged(
        self, monkeypatch, repo
    ):
        calls = _install_fake_subprocess(monkeypatch)
        before = _snapshot(repo)

        client = _RecordingOpenCodeClient()
        jev = JEV(client=client)
        model = _free_model()

        task = JEVTask(
            prompt="inspect the repository",
            workdir=str(repo),
            policy=READ_ONLY,
        )
        result = jev.run(task, available_models=[model])

        assert result.success
        assert calls  # the read-only execution actually ran
        assert _snapshot(repo) == before
