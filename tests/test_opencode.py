from pathlib import Path
import json

import pytest

from providers.opencode import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeTimeoutError,
)


def test_run_builds_expected_command(monkeypatch, tmp_path):
    calls = []

    class Completed:
        returncode = 0
        stdout = json.dumps(
            {
                "type": "text",
                "part": {"text": "hello"},
            }
        )
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return Completed()

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        fake_run,
    )

    client = OpenCodeClient(
        executable="/test/opencode",
        timeout=42,
    )

    result = client.run(
        "hello",
        tmp_path,
        model="opencode/test-free",
    )

    assert result.success
    assert result.text == "hello"

    command, kwargs = calls[0]

    assert command == [
        "/test/opencode",
        "run",
        "--format",
        "json",
        "--model",
        "opencode/test-free",
        "hello",
    ]

    assert kwargs["cwd"] == Path(tmp_path)
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 42
    assert kwargs["check"] is False


def test_run_uses_default_model(monkeypatch, tmp_path):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        return Completed()

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        fake_run,
    )

    OpenCodeClient().run("hello", tmp_path)

    assert "opencode/mimo-v2.5-free" in captured["command"]


def test_empty_prompt_rejected(tmp_path):
    with pytest.raises(ValueError):
        OpenCodeClient().run("   ", tmp_path)


def test_json_events_are_parsed(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stdout = "\n".join(
            [
                '{"type":"step_start","part":{"id":"one"}}',
                '{"type":"text","part":{"text":"hello"}}',
                '{"type":"tool_use","part":{"tool":"read"}}',
                "not-json",
            ]
        )
        stderr = ""

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    result = OpenCodeClient().run("hello", tmp_path)

    assert [event.type for event in result.events] == [
        "step_start",
        "text",
        "tool_use",
    ]
    assert result.text == "hello"


def test_nonzero_exit_is_preserved(monkeypatch, tmp_path):
    class Completed:
        returncode = 1
        stdout = ""
        stderr = "failure"

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        lambda *args, **kwargs: Completed(),
    )

    result = OpenCodeClient().run("hello", tmp_path)

    assert result.success is False
    assert result.exit_code == 1
    assert result.stderr == "failure"


def test_generic_timeout_is_not_misclassified(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        fake_run,
    )

    with pytest.raises(OpenCodeError):
        OpenCodeClient().run("hello", tmp_path)


def test_timeout_expired_becomes_specific_error(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(
            cmd=["opencode"],
            timeout=1,
        )

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        fake_run,
    )

    with pytest.raises(OpenCodeTimeoutError):
        OpenCodeClient().run("hello", tmp_path)


def test_os_error_becomes_opencode_error(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise OSError("missing executable")

    monkeypatch.setattr(
        "providers.opencode.subprocess.run",
        fake_run,
    )

    with pytest.raises(OpenCodeError):
        OpenCodeClient().run("hello", tmp_path)
