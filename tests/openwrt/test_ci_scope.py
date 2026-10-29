"""Tests for the conservative VM skip rule, without any GitHub API calls."""
import unittest
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

    def test_ubuntu_rename_into_openwrt_still_requires_vm(self):
        with tempfile.TemporaryDirectory(prefix='pw-ci-scope-') as work:
            root = Path(work)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True, stderr=subprocess.DEVNULL).strip()
            git('init', '-q')
            git('config', 'user.name', 'scope-test')
            git('config', 'user.email', 'scope-test@users.noreply.github.com')
            (root / 'vps-bootstrap-ubuntu24.sh').write_text('unchanged content\n')
            git('add', '.')
            git('commit', '-qm', 'Synthetic baseline')
            base = git('rev-parse', 'HEAD')
            (root / 'openwrt').mkdir()
            git('mv', 'vps-bootstrap-ubuntu24.sh', 'openwrt/moved.sh')
            git('commit', '-qm', 'Synthetic cross-scope rename')
            output = root / 'output'
            subprocess.run([sys.executable, str(Path(__file__).with_name('ci_scope.py'))], cwd=root,
                           env={**os.environ, 'GITHUB_EVENT_NAME': 'pull_request',
                                'PR_BASE_SHA': base, 'PR_HEAD_SHA': git('rev-parse', 'HEAD'),
                                'GITHUB_OUTPUT': str(output)}, capture_output=True, check=True)
            self.assertEqual(output.read_text(), 'ubuntu_vm=true\n')


if __name__ == '__main__':
    unittest.main()
