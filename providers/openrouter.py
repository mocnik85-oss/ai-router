from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import Config
from providers.credentials import CredentialError, CredentialStore


class OpenRouterError(Exception):
    """Base class for OpenRouter client errors."""


class OpenRouterAuthError(OpenRouterError):
    """Raised when OpenRouter rejects authentication."""


class OpenRouterRateLimitError(OpenRouterError):
    """Raised when OpenRouter rate-limits a request."""


class OpenRouterTimeoutError(OpenRouterError):
    """Raised when an OpenRouter request times out."""


class OpenRouterConnectionError(OpenRouterError):
    """Raised when OpenRouter cannot be reached."""


class OpenRouterServerError(OpenRouterError):
    """Raised when OpenRouter returns a server-side error."""


class OpenRouterModelError(OpenRouterError):
    """Raised when the requested model is unavailable or invalid."""


class OpenRouterResponseError(OpenRouterError):
    """Raised when OpenRouter returns an unexpected response."""


@dataclass(slots=True)
class OpenRouterResponse:
    """Normalized response from an OpenRouter chat completion."""

    model: str
    content: str
    raw: dict[str, Any]


class OpenRouterClient:
    """Minimal OpenRouter chat-completions client."""

    BASE_URL = "https://openrouter.ai/api/v1"
    PROVIDER_NAME = "openrouter"

    def __init__(
        self,
        config: Config,
        credentials: CredentialStore,
        *,
        base_url: str | None = None,
    ) -> None:
        self.config = config
        self.credentials = credentials
        self.base_url = (base_url or self.BASE_URL).rstrip("/")

    def _api_key(self) -> str:
        try:
            return self.credentials.get(self.PROVIDER_NAME)
        except CredentialError as exc:
            raise OpenRouterAuthError(
                "No OpenRouter credential is configured."
            ) from exc

    def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        api_key = self._api_key()

        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=timeout if timeout is not None else self.config.timeout,
            ) as response:
                body = response.read()
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise OpenRouterAuthError(
                    "OpenRouter authentication failed."
                ) from exc
            if exc.code == 429:
                raise OpenRouterRateLimitError(
                    "OpenRouter rate limit reached."
                ) from exc
            if exc.code == 404:
                raise OpenRouterModelError(
                    "OpenRouter model or endpoint was not found."
                ) from exc
            if 500 <= exc.code <= 599:
                raise OpenRouterServerError(
                    f"OpenRouter server error: HTTP {exc.code}."
                ) from exc
            raise OpenRouterError(
                f"OpenRouter request failed: HTTP {exc.code}."
            ) from exc
        except TimeoutError as exc:
            raise OpenRouterTimeoutError(
                "OpenRouter request timed out."
            ) from exc
        except URLError as exc:
            raise OpenRouterConnectionError(
                "Unable to connect to OpenRouter."
            ) from exc

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OpenRouterResponseError(
                "OpenRouter returned invalid JSON."
            ) from exc

        if not isinstance(decoded, dict):
            raise OpenRouterResponseError(
                "OpenRouter returned an unexpected response."
            )

        return decoded

    def chat(
        self,
        prompt: str,
        *,
        model: str | None = None,
    ) -> OpenRouterResponse:
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        selected_model = model or self.config.preferred_model

        if not selected_model:
            raise OpenRouterModelError(
                "No OpenRouter model was specified."
            )

        if self.config.free_only and not selected_model.endswith(":free"):
            raise OpenRouterModelError(
                "free_only=true requires a model explicitly identified as free."
            )

        payload = {
            "model": selected_model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
        }

        data = self._request(payload)

        try:
            choices = data["choices"]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OpenRouterResponseError(
                "OpenRouter response did not contain a chat message."
            ) from exc

        if not isinstance(content, str):
            raise OpenRouterResponseError(
                "OpenRouter returned non-text content."
            )

        response_model = data.get("model", selected_model)

        return OpenRouterResponse(
            model=str(response_model),
            content=content,
            raw=data,
        )
