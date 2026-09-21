import unittest

from router.policy import READ_ONLY, ExecutionPolicy


class ExecutionPolicyDefaultsTests(unittest.TestCase):
    """Verify the default values of a bare ExecutionPolicy."""

    def test_read_defaults_true(self):
        self.assertTrue(ExecutionPolicy().read)

    def test_edit_defaults_false(self):
        self.assertFalse(ExecutionPolicy().edit)

    def test_shell_defaults_false(self):
        self.assertFalse(ExecutionPolicy().shell)

    def test_network_defaults_false(self):
        self.assertFalse(ExecutionPolicy().network)

    def test_git_defaults_false(self):
        self.assertFalse(ExecutionPolicy().git)

    def test_commit_defaults_false(self):
        self.assertFalse(ExecutionPolicy().commit)

    def test_workspace_defaults_none(self):
        self.assertIsNone(ExecutionPolicy().workspace)

    def test_timeout_defaults_120(self):
        self.assertEqual(ExecutionPolicy().timeout, 120.0)

    def test_max_attempts_defaults_1(self):
        self.assertEqual(ExecutionPolicy().max_attempts, 1)


class ReadOnlyPresetTests(unittest.TestCase):
    """Verify the READ_ONLY preset matches the spec."""

    def test_is_instance(self):
        self.assertIsInstance(READ_ONLY, ExecutionPolicy)

    def test_read_true(self):
        self.assertTrue(READ_ONLY.read)

    def test_edit_false(self):
        self.assertFalse(READ_ONLY.edit)

    def test_shell_false(self):
        self.assertFalse(READ_ONLY.shell)

    def test_network_false(self):
        self.assertFalse(READ_ONLY.network)

    def test_git_false(self):
        self.assertFalse(READ_ONLY.git)

    def test_commit_false(self):
        self.assertFalse(READ_ONLY.commit)

    def test_workspace_none(self):
        self.assertIsNone(READ_ONLY.workspace)

    def test_timeout_120(self):
        self.assertEqual(READ_ONLY.timeout, 120.0)

    def test_max_attempts_1(self):
        self.assertEqual(READ_ONLY.max_attempts, 1)


class ExecutionPolicyImmutabilityTests(unittest.TestCase):
    """Frozen dataclass should reject mutation."""

    def test_cannot_set_read(self):
        with self.assertRaises(AttributeError):
            READ_ONLY.read = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
