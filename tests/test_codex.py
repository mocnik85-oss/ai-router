from pathlib import Path

import pytest

from providers.codex import (
    CodexClient,
    CodexError,
    CodexResult,
    CodexTimeoutError,
)


def test_result_success() -> None:
    result = CodexResult(
        exit_code=0,
        stdout="done",
        stderr="",
    )

    assert result.success is True


def test_result_failure() -> None:
    result = CodexResult(
        exit_code=1,
        stdout="",
        stderr="error",
    )

    assert result.success is False


def test_empty_prompt_rejected() -> None:
    client = CodexClient()

    with pytest.raises(ValueError):
        client.run("")


def test_whitespace_prompt_rejected() -> None:
    client = CodexClient()

    with pytest.raises(ValueError):
        client.run("   ")


def test_invalid_working_directory_rejected(tmp_path: Path) -> None:
    client = CodexClient()

    missing = tmp_path / "missing"

    with pytest.raises(ValueError):
        client.run("test", missing)


def test_timeout_validation() -> None:
    with pytest.raises(ValueError):
        CodexClient(timeout=0)


def test_command_construction(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

        class Completed:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Completed()

    monkeypatch.setattr("providers.codex.subprocess.run", fake_run)

    client = CodexClient(
        executable="/custom/codex",
        timeout=42,
    )

    result = client.run(
        "implement the thing",
        tmp_path,
    )

    assert result.success is True
    assert captured["args"][0] == [
        "/custom/codex",
        "exec",
        "implement the thing",
    ]

    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == tmp_path.resolve()
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["timeout"] == 42
    assert kwargs["check"] is False


def test_timeout_is_wrapped(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        raise __import__("subprocess").TimeoutExpired(
            cmd=args[0],
            timeout=42,
        )

    monkeypatch.setattr("providers.codex.subprocess.run", fake_run)

    client = CodexClient(timeout=42)

    with pytest.raises(CodexTimeoutError):
        client.run("test", tmp_path)


def test_start_failure_is_wrapped(monkeypatch, tmp_path: Path) -> None:
    def fake_run(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr("providers.codex.subprocess.run", fake_run)

    client = CodexClient()

    with pytest.raises(CodexError):
        client.run("test", tmp_path)
