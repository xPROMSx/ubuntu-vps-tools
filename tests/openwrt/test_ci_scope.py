"""Tests for the conservative VM skip rule, without any GitHub API calls."""
import unittest
from ci_scope import requires_ubuntu_vm


class ScopeTests(unittest.TestCase):
    def test_openwrt_only(self):
        self.assertFalse(requires_ubuntu_vm(['openwrt/passwall2-apk-repo.sh', 'tests/openwrt/test_manager.py', 'README.md'], 'pull_request'))

    def test_shared_or_ci_changes_require_vm(self):
        for path in ['vps-bootstrap-ubuntu24.sh', 'tests/ci/basic.py', 'tests/ci/vm.py', '.github/workflows/ci.yml', 'tests/openwrt/ci_scope.py', 'tests/openwrt/test_ci_scope.py', 'update-sshid', 'unknown']:
            with self.subTest(path=path):
                self.assertTrue(requires_ubuntu_vm(['openwrt/passwall2-apk-repo.sh', path], 'pull_request'))

    def test_push_or_empty_range_requires_vm(self):
        self.assertTrue(requires_ubuntu_vm(['openwrt/passwall2-apk-repo.sh'], 'push'))
        self.assertTrue(requires_ubuntu_vm([], 'pull_request'))


if __name__ == '__main__':
    unittest.main()
