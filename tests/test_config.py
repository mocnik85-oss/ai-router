import tempfile
import unittest
from pathlib import Path

from app.config import Config, ConfigError


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Config.load(Path(tmp) / "missing.toml")

        self.assertIsNone(config.preferred_provider)
        self.assertIsNone(config.preferred_model)
        self.assertEqual(config.fallback_models, [])
        self.assertEqual(config.timeout, 30.0)
        self.assertEqual(config.retries, 2)
        self.assertTrue(config.free_only)
        self.assertFalse(config.paid_routing_enabled)
        self.assertFalse(config.voice_enabled)
        self.assertFalse(config.tts_enabled)

    def test_valid_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                """
preferred_provider = "openrouter"
preferred_model = "some-model"
fallback_models = ["model-a", "model-b"]
timeout = 45
retries = 3
fallback_conditions = ["rate_limit", "timeout"]
free_only = true
paid_routing_enabled = false
voice_enabled = true
stt_engine = "local"
stt_model = "small"
tts_enabled = false
tts_engine = "local"
logging = "debug"
""",
                encoding="utf-8",
            )

            config = Config.load(path)

        self.assertEqual(config.preferred_provider, "openrouter")
        self.assertEqual(config.preferred_model, "some-model")
        self.assertEqual(config.fallback_models, ["model-a", "model-b"])
        self.assertEqual(config.timeout, 45.0)
        self.assertEqual(config.retries, 3)
        self.assertTrue(config.voice_enabled)
        self.assertEqual(config.logging, "debug")

    def test_unknown_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                'something_unknown = "value"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                Config.load(path)

    def test_credential_like_field_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                'api_key = "this-must-never-be-here"\n',
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                Config.load(path)

    def test_invalid_timeout_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"timeout": -1})

    def test_invalid_retries_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_dict({"retries": -1})

    def test_paid_and_free_only_conflict_rejected(self):
        with self.assertRaises(ConfigError):
            Config.from_dict(
                {
                    "free_only": True,
                    "paid_routing_enabled": True,
                }
            )

    def test_serialization_contains_no_credentials(self):
        config = Config()
        serialized = config.to_dict()

        for key in serialized:
            lowered = key.lower()
            self.assertFalse(
                any(
                    secret_word in lowered
                    for secret_word in (
                        "api_key",
                        "apikey",
                        "token",
                        "secret",
                        "password",
                        "credential",
                    )
                )
            )


if __name__ == "__main__":
    unittest.main()
