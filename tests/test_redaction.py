import unittest

from app.redaction import redact


class RedactionTests(unittest.TestCase):
    def test_api_key(self):
        result = redact("api_key=super-secret-value")

        self.assertNotIn("super-secret-value", result)
        self.assertIn("[REDACTED]", result)

    def test_token(self):
        result = redact("token: abc123secret")

        self.assertNotIn("abc123secret", result)
        self.assertIn("[REDACTED]", result)

    def test_password(self):
        result = redact("password=my-password")

        self.assertNotIn("my-password", result)
        self.assertIn("[REDACTED]", result)

    def test_bearer_token(self):
        result = redact(
            "Authorization: Bearer extremely-secret-token"
        )

        self.assertNotIn("extremely-secret-token", result)
        self.assertIn("[REDACTED]", result)

    def test_normal_text_unchanged(self):
        text = "Request failed with HTTP 429"

        self.assertEqual(redact(text), text)


if __name__ == "__main__":
    unittest.main()
