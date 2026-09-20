from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.config import Config
from providers.credentials import MemoryCredentialStore
from providers.openrouter import (
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterModelError,
    OpenRouterRateLimitError,
    OpenRouterResponseError,
    OpenRouterServerError,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.body


class FakeHTTPErrorResponse:
    def __init__(self, code: int) -> None:
        self.code = code
        self.reason = "test error"


def make_client() -> OpenRouterClient:
    config = Config(
        preferred_provider="openrouter",
        preferred_model="test-model:free",
        fallback_models=[],
        timeout=5.0,
        retries=0,
        fallback_conditions=[
            "rate_limit",
            "timeout",
            "connection_error",
            "server_error",
            "model_unavailable",
        ],
        free_only=True,
        paid_routing_enabled=False,
        voice_enabled=False,
        stt_engine=None,
        stt_model=None,
        tts_enabled=False,
        tts_engine=None,
        logging="info",
    )

    credentials = MemoryCredentialStore()
    credentials.set("openrouter", "TEST_KEY_DO_NOT_LEAK")

    return OpenRouterClient(
        config,
        credentials,
        base_url="https://example.test/api/v1",
    )


class OpenRouterClientTests(unittest.TestCase):
    def test_successful_chat(self):
        client = make_client()

        payload = {
            "model": "test-model:free",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Hello from the test model.",
                    }
                }
            ],
        }

        with patch(
            "providers.openrouter.urlopen",
            return_value=FakeHTTPResponse(payload),
        ) as mock_urlopen:
            response = client.chat("Hello")

        self.assertEqual(response.model, "test-model:free")
        self.assertEqual(response.content, "Hello from the test model.")
        mock_urlopen.assert_called_once()

        request = mock_urlopen.call_args.args[0]

        self.assertEqual(
            request.full_url,
            "https://example.test/api/v1/chat/completions",
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer TEST_KEY_DO_NOT_LEAK",
        )

        sent_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent_payload["model"], "test-model:free")
        self.assertEqual(
            sent_payload["messages"],
            [{"role": "user", "content": "Hello"}],
        )

    def test_empty_prompt_rejected(self):
        client = make_client()

        with self.assertRaises(ValueError):
            client.chat("   ")

    def test_paid_model_rejected_when_free_only(self):
        client = make_client()

        with self.assertRaises(OpenRouterModelError):
            client.chat("Hello", model="paid-model")

    def test_missing_model_rejected(self):
        client = make_client()
        client.config.preferred_model = None

        with self.assertRaises(OpenRouterModelError):
            client.chat("Hello")

    def test_auth_error(self):
        client = make_client()

        def raise_auth(*args, **kwargs):
            from urllib.error import HTTPError

            raise HTTPError(
                url="https://example.test",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=None,
            )

        with patch(
            "providers.openrouter.urlopen",
            side_effect=raise_auth,
        ):
            with self.assertRaises(OpenRouterAuthError):
                client.chat("Hello")

    def test_rate_limit_error(self):
        client = make_client()

        def raise_rate_limit(*args, **kwargs):
            from urllib.error import HTTPError

            raise HTTPError(
                url="https://example.test",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=None,
            )

        with patch(
            "providers.openrouter.urlopen",
            side_effect=raise_rate_limit,
        ):
            with self.assertRaises(OpenRouterRateLimitError):
                client.chat("Hello")

    def test_server_error(self):
        client = make_client()

        def raise_server_error(*args, **kwargs):
            from urllib.error import HTTPError

            raise HTTPError(
                url="https://example.test",
                code=500,
                msg="Server Error",
                hdrs=None,
                fp=None,
            )

        with patch(
            "providers.openrouter.urlopen",
            side_effect=raise_server_error,
        ):
            with self.assertRaises(OpenRouterServerError):
                client.chat("Hello")

    def test_invalid_json_rejected(self):
        client = make_client()

        class BadResponse(FakeHTTPResponse):
            def __init__(self):
                self.body = b"not-json"

        with patch(
            "providers.openrouter.urlopen",
            return_value=BadResponse(),
        ):
            with self.assertRaises(OpenRouterResponseError):
                client.chat("Hello")

    def test_missing_choices_rejected(self):
        client = make_client()

        with patch(
            "providers.openrouter.urlopen",
            return_value=FakeHTTPResponse({}),
        ):
            with self.assertRaises(OpenRouterResponseError):
                client.chat("Hello")

    def test_api_key_is_not_in_response(self):
        client = make_client()

        payload = {
            "model": "test-model:free",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Safe response.",
                    }
                }
            ],
        }

        with patch(
            "providers.openrouter.urlopen",
            return_value=FakeHTTPResponse(payload),
        ):
            response = client.chat("Hello")

        self.assertNotIn("TEST_KEY_DO_NOT_LEAK", response.content)





if __name__ == "__main__":
    unittest.main()

if __name__ == "__main__":
    unittest.main()

    def test_model_discovery(self):
        client = make_client()

        payload = {
            "data": [
                {
                    "id": "free-model:free",
                    "name": "Free Model",
                    "context_length": 128000,
                    "pricing": {
                        "prompt": "0",
                        "completion": "0",
                    },
                    "architecture": {
                        "input_modalities": ["text", "image"],
                    },
                    "supported_parameters": ["tools"],
                },
                {
                    "id": "paid-model",
                    "name": "Paid Model",
                    "context_length": 64000,
                    "pricing": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                    },
                    "architecture": {
                        "input_modalities": ["text"],
                    },
                    "supported_parameters": [],
                },
            ]
        }

        with patch(
            "providers.openrouter.urlopen",
            return_value=FakeHTTPResponse(payload),
        ):
            models = client.models()

        self.assertEqual(len(models), 2)
        self.assertEqual(models[0].id, "free-model:free")
        self.assertTrue(models[0].is_free)
        self.assertFalse(models[1].is_free)
        self.assertTrue(models[0].supports_tools)
        self.assertTrue(models[0].supports_vision)

    def test_free_model_filter(self):
        client = make_client()

        payload = {
            "data": [
                {
                    "id": "free-one:free",
                    "name": "Free One",
                    "context_length": 128000,
                    "pricing": {
                        "prompt": "0",
                        "completion": "0",
                    },
                },
                {
                    "id": "paid-one",
                    "name": "Paid One",
                    "context_length": 64000,
                    "pricing": {
                        "prompt": "0.000001",
                        "completion": "0.000002",
                    },
                },
            ]
        }

        with patch(
            "providers.openrouter.urlopen",
            return_value=FakeHTTPResponse(payload),
        ):
            models = client.free_models()

        self.assertEqual(len(models), 1)
        self.assertEqual(models[0].id, "free-one:free")
