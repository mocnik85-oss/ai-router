"""Tests for router.jev — JEV v0.1 orchestration layer.

All external calls (OpenCodeClient, model selection) are mocked.
No real network or model calls are made.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from providers.opencode import OpenCodeClient, OpenCodeResult, OpenCodeEvent
from providers.openrouter import OpenRouterModel
from router.free_controller import FreeModelController, ModelRequirements
from router.jev import JEV, JEVTask, JEVResult, JEVError, PolicyViolationError
from router.policy import ExecutionPolicy, READ_ONLY


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_model(
    model_id: str = "opencode/test-free-model",
    *,
    context: int = 100_000,
    tools: bool = True,
) -> OpenRouterModel:
    return OpenRouterModel(
        id=model_id,
        name=model_id,
        context_length=context,
        prompt_price="0",
        completion_price="0",
        supports_tools=tools,
        supports_vision=False,
        raw={},
    )


def _make_oc_result(
    *,
    exit_code: int = 0,
    text: str = "output",
    stderr: str = "",
) -> OpenCodeResult:
    events: list[OpenCodeEvent] = []

    if text:
        events.append(
            OpenCodeEvent(
                type="text",
                raw={"part": {"text": text}},
            )
        )

    return OpenCodeResult(
        exit_code=exit_code,
        events=events,
        stdout=text,
        stderr=stderr,
    )


class _FakeController(FreeModelController):
    """Controller that always returns a fixed model, bypassing real scoring."""

    def __init__(self, model: OpenRouterModel) -> None:
        self._model = model

    def select(
        self,
        models: list[OpenRouterModel],
        requirements: ModelRequirements | None = None,
    ) -> OpenRouterModel | None:
        return self._model


class _FakeClient(OpenCodeClient):
    """Client that returns a canned result instead of shelling out."""

    def __init__(self, result: OpenCodeResult) -> None:
        super().__init__()
        self._result = result

    def run(
        self,
        prompt: str,
        workdir: str | Path,
        *,
        model: str | None = None,
        policy: ExecutionPolicy | None = None,
    ) -> OpenCodeResult:
        return self._result

    def can_route(self, model_id: str) -> bool:  # noqa: D401
        """Accept all model IDs — test double bypasses the real filter."""
        return True


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

class TestJEVValidation(unittest.TestCase):
    """Policy validation is checked before any model selection."""

    def test_read_only_policy_accepted(self) -> None:
        client = _FakeClient(_make_oc_result())
        model = _make_model()
        controller = _FakeController(model)

        jev = JEV(client=client, controller=controller)

        task = JEVTask(
            prompt="list files",
            workdir="/tmp",
            policy=READ_ONLY,
        )

        result = jev.run(task, available_models=[model])

        self.assertTrue(result.success)

    def test_edit_policy_rejected(self) -> None:
        policy = ExecutionPolicy(edit=True)
        task = JEVTask(prompt="modify x", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError):
            jev.run(task)

    def test_shell_policy_rejected(self) -> None:
        policy = ExecutionPolicy(shell=True)
        task = JEVTask(prompt="run rm", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError):
            jev.run(task)

    def test_network_policy_rejected(self) -> None:
        policy = ExecutionPolicy(network=True)
        task = JEVTask(prompt="fetch url", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError):
            jev.run(task)

    def test_git_policy_rejected(self) -> None:
        policy = ExecutionPolicy(git=True)
        task = JEVTask(prompt="git status", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError):
            jev.run(task)

    def test_commit_policy_rejected(self) -> None:
        policy = ExecutionPolicy(commit=True)
        task = JEVTask(prompt="commit changes", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError):
            jev.run(task)

    def test_multiple_forbidden_flags_rejected(self) -> None:
        policy = ExecutionPolicy(edit=True, shell=True, network=True)
        task = JEVTask(prompt="do everything", workdir="/tmp", policy=policy)

        jev = JEV()

        with self.assertRaises(PolicyViolationError) as ctx:
            jev.run(task)

        msg = str(ctx.exception)
        self.assertIn("edit", msg)
        self.assertIn("shell", msg)
        self.assertIn("network", msg)


class TestJEVModelSelection(unittest.TestCase):
    """Model selection via the FreeModelController."""

    def test_no_model_returns_failure(self) -> None:
        """When no free model is available, JEV returns a failure result."""

        class NoModelController(FreeModelController):
            def select(self, models, requirements=None):
                return None

        client = _FakeClient(_make_oc_result())
        jev = JEV(client=client, controller=NoModelController())

        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task, available_models=[_make_model()])

        self.assertFalse(result.success)
        self.assertIn("No suitable free model", result.error)
        self.assertEqual(result.model, "")

    def test_selected_model_passed_to_client(self) -> None:
        """The model id chosen by the controller is forwarded to OpenCodeClient."""

        model = _make_model(model_id="opencode/chosen-model-1")
        client = _FakeClient(_make_oc_result())
        controller = _FakeController(model)

        jev = JEV(client=client, controller=controller)
        task = JEVTask(prompt="inspect", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertTrue(result.success)
        self.assertEqual(result.model, "opencode/chosen-model-1")

    def test_empty_model_list_yields_no_model(self) -> None:
        client = _FakeClient(_make_oc_result())
        jev = JEV(client=client)

        task = JEVTask(prompt="hello", workdir="/tmp")
        result = jev.run(task, available_models=[])

        self.assertFalse(result.success)
        self.assertIn("No suitable free model", result.error)


class TestJEVExecution(unittest.TestCase):
    """End-to-end execution with mocked client and controller."""

    def test_successful_execution(self) -> None:
        model = _make_model()
        client = _FakeClient(
            _make_oc_result(exit_code=0, text="hello world")
        )
        controller = _FakeController(model)

        jev = JEV(client=client, controller=controller)
        task = JEVTask(prompt="say hello", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.text, "hello world")
        self.assertEqual(result.model, model.id)

    def test_nonzero_exit_preserved(self) -> None:
        model = _make_model()
        client = _FakeClient(
            _make_oc_result(exit_code=1, text="", stderr="err")
        )
        controller = _FakeController(model)

        jev = JEV(client=client, controller=controller)
        task = JEVTask(prompt="fail", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)

    def test_client_exception_returns_error(self) -> None:
        """If the client raises, JEV catches it and returns a failure."""

        class ExplodingClient(OpenCodeClient):
            def run(self, prompt, workdir, *, model=None, policy=None):
                raise OSError("subprocess missing")

            def can_route(self, model_id: str) -> bool:
                return True

        model = _make_model()
        jev = JEV(client=ExplodingClient(), controller=_FakeController(model))

        task = JEVTask(prompt="boom", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertFalse(result.success)
        self.assertIn("subprocess missing", result.error)

    def test_events_are_serialised(self) -> None:
        model = _make_model()
        raw_event = {"type": "tool_use", "part": {"tool": "read"}}
        oc_result = OpenCodeResult(
            exit_code=0,
            events=[
                OpenCodeEvent(type="text", raw={"part": {"text": "ok"}}),
                OpenCodeEvent(type="tool_use", raw=raw_event),
            ],
            stdout="ok",
            stderr="",
        )

        client = _FakeClient(oc_result)
        controller = _FakeController(model)

        jev = JEV(client=client, controller=controller)
        task = JEVTask(prompt="read file", workdir="/tmp")
        result = jev.run(task, available_models=[model])

        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0]["type"], "text")
        self.assertEqual(result.events[1]["type"], "tool_use")


class TestJEVDefaultClient(unittest.TestCase):
    """Verify JEV constructs a default client when none is provided."""

    def test_default_controller_is_free_model_controller(self) -> None:
        jev = JEV()
        self.assertIsInstance(jev._controller, FreeModelController)

    def test_default_client_is_opencode_client(self) -> None:
        jev = JEV()
        self.assertIsInstance(jev._client, OpenCodeClient)


if __name__ == "__main__":
    unittest.main()
