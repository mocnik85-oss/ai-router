from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import tomllib


class ConfigError(ValueError):
    """Raised when ai-router configuration is invalid."""


DEFAULT_CONFIG_PATH = (
    Path.home() / ".config" / "ai-router" / "config.toml"
)

FORBIDDEN_KEY_PARTS = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
)


@dataclass(slots=True)
class Config:
    preferred_provider: str | None = None
    preferred_model: str | None = None
    fallback_models: list[str] = field(default_factory=list)

    timeout: float = 30.0
    retries: int = 2
    fallback_conditions: list[str] = field(
        default_factory=lambda: [
            "rate_limit",
            "timeout",
            "connection_error",
            "server_error",
            "model_unavailable",
        ]
    )

    free_only: bool = True
    paid_routing_enabled: bool = False

    voice_enabled: bool = False
    stt_engine: str | None = None
    stt_model: str | None = None

    tts_enabled: bool = False
    tts_engine: str | None = None

    logging: str = "info"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_CONFIG_PATH

        if not path.exists():
            return cls()

        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML configuration: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Unable to read configuration: {exc}") from exc

        _check_forbidden_keys(raw)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        if not isinstance(raw, dict):
            raise ConfigError("Configuration root must be a table/object.")

        allowed = {
            "preferred_provider",
            "preferred_model",
            "fallback_models",
            "timeout",
            "retries",
            "fallback_conditions",
            "free_only",
            "paid_routing_enabled",
            "voice_enabled",
            "stt_engine",
            "stt_model",
            "tts_enabled",
            "tts_engine",
            "logging",
        }

        unknown = set(raw) - allowed
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"Unknown configuration field(s): {names}")

        values = cls()

        for key, value in raw.items():
            setattr(values, key, value)

        values.validate()
        return values

    def validate(self) -> None:
        if self.preferred_provider is not None:
            _require_string(self.preferred_provider, "preferred_provider")

        if self.preferred_model is not None:
            _require_string(self.preferred_model, "preferred_model")

        _require_string_list(self.fallback_models, "fallback_models")

        if not isinstance(self.timeout, (int, float)) or isinstance(
            self.timeout, bool
        ):
            raise ConfigError("timeout must be a number.")

        if self.timeout <= 0:
            raise ConfigError("timeout must be greater than zero.")

        if not isinstance(self.retries, int) or isinstance(self.retries, bool):
            raise ConfigError("retries must be an integer.")

        if self.retries < 0:
            raise ConfigError("retries cannot be negative.")

        _require_string_list(
            self.fallback_conditions,
            "fallback_conditions",
        )

        for name in (
            "free_only",
            "paid_routing_enabled",
            "voice_enabled",
            "tts_enabled",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ConfigError(f"{name} must be true or false.")

        for name in ("stt_engine", "stt_model", "tts_engine"):
            value = getattr(self, name)
            if value is not None:
                _require_string(value, name)

        if self.logging not in {
            "debug",
            "info",
            "warning",
            "error",
            "critical",
        }:
            raise ConfigError(
                "logging must be one of: "
                "debug, info, warning, error, critical."
            )

        if self.free_only and self.paid_routing_enabled:
            raise ConfigError(
                "free_only=true cannot be combined with "
                "paid_routing_enabled=true."
            )

    def to_dict(self) -> dict[str, Any]:
        """Return only ordinary, non-secret configuration."""
        return asdict(self)


def _require_string(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise ConfigError(f"{name} must be a string.")


def _require_string_list(value: Any, name: str) -> None:
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list.")

    if not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must contain only strings.")


def _check_forbidden_keys(data: dict[str, Any]) -> None:
    for key in data:
        lowered = key.lower()

        if any(part in lowered for part in FORBIDDEN_KEY_PARTS):
            raise ConfigError(
                f"Forbidden credential-like field: {key}"
            )
