import unittest

from providers.credentials import (
    CredentialNotFoundError,
    CredentialError,
    MemoryCredentialStore,
)


class CredentialStoreTests(unittest.TestCase):
    def test_missing_credential(self):
        store = MemoryCredentialStore()

        with self.assertRaises(CredentialNotFoundError):
            store.get("openrouter")

    def test_store_and_retrieve(self):
        store = MemoryCredentialStore()

        store.set("openrouter", "test-secret")

        self.assertEqual(
            store.get("openrouter"),
            "test-secret",
        )

    def test_delete(self):
        store = MemoryCredentialStore()

        store.set("openai", "test-secret")
        store.delete("openai")

        with self.assertRaises(CredentialNotFoundError):
            store.get("openai")

    def test_empty_provider_rejected(self):
        store = MemoryCredentialStore()

        with self.assertRaises(CredentialError):
            store.set("", "test-secret")

    def test_empty_credential_rejected(self):
        store = MemoryCredentialStore()

        with self.assertRaises(CredentialError):
            store.set("openrouter", "")


if __name__ == "__main__":
    unittest.main()
