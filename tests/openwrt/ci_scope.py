"""Conservative PR path filter; changes to CI/shared tests always require VMs."""
import os
import subprocess


def requires_ubuntu_vm(paths, event):
    if event != 'pull_request' or not paths:
        return True
    if any(p in {'tests/openwrt/ci_scope.py', 'tests/openwrt/test_ci_scope.py'} for p in paths):
        return True
    related_docs = {'README.md', 'AGENTS.md', 'CLAUDE.md', 'tests/ci/README.md'}
    return any(not (p.startswith(('openwrt/', 'tests/openwrt/')) or p in related_docs) for p in paths)


if __name__ == '__main__':
    event = os.environ['GITHUB_EVENT_NAME']
    paths = []
    if event == 'pull_request':
        # The preceding metadata check validates the trusted full endpoints.
        # Include both deleted and added paths when a file crosses scope by rename.
        data = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '-z',
                                        os.environ['PR_BASE_SHA'] + '...' + os.environ['PR_HEAD_SHA']])
        paths = [p.decode('utf-8') for p in data.split(b'\0') if p]
    value = str(requires_ubuntu_vm(paths, event)).lower()
    with open(os.environ['GITHUB_OUTPUT'], 'a') as out:
        out.write('ubuntu_vm=' + value + '\n')
    print('Ubuntu VM required: ' + value)
