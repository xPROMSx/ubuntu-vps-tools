"""Isolated updater regressions; no host SSH/systemd changes."""
import errno
import os
import pathlib
import pty
import select
import time
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'update-sshid').read_text()
BOOT = (ROOT / 'vps-bootstrap-ubuntu24.sh').read_text()
GENERATED = re.search("cat <<'UPDATER'\n(.*?)\nUPDATER", BOOT, re.S)[1]

with tempfile.TemporaryDirectory() as directory:
    root = pathlib.Path(directory)
    def mapped(text):
        return (text.replace('/root/.ssh', str(root / 'ssh'))
                .replace('/root/backups', str(root / 'backups'))
                .replace('/etc/ssh', str(root / 'etc'))
                .replace('/run/lock/vps-bootstrap-proms.lock', str(root / 'bootstrap.lock'))
                .replace('[[ $EUID -eq 0 ]]', 'true'))
    script = root / 'updater'
    script.write_text(mapped(SOURCE))
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(root/'key')], check=True)
    personal = re.search("PERSONAL_KEY='(.*?)'", SOURCE)[1]
    payload = root / 'payload'
    payload.write_text((root/'key.pub').read_text() + personal + '\n' + personal + '\n')
    pre = f'source {script}; chown() {{ :; }}; curl() {{ cat {payload}; }}; ' + '''install() { local args=(); while (($#)); do case $1 in -o|-g) shift 2;; *) args+=("$1"); shift;; esac; done; command install "${args[@]}"; }; '''
    def run(code, ok=True):
        result = subprocess.run(['bash','-c',code], capture_output=True, text=True)
        assert (result.returncode == 0) == ok, result.stderr
        return result
    run(pre + 'update_keys')
    keys = root/'ssh/authorized_keys'
    first = keys.read_bytes()
    inode = keys.stat().st_ino
    assert first.count(personal.encode()) == 1
    assert all(marker in first for marker in (b'# BEGIN SSH ID', b'# BEGIN PERSONAL ED25519', b'# BEGIN LUMA YUBIKEY'))
    assert first.count(b'sk-ssh-ed25519@openssh.com ') == 2
    run(pre + 'update_keys')
    assert keys.stat().st_ino == inode and keys.read_bytes() == first
    run(pre + mapped(GENERATED))
    assert keys.read_bytes() == first and keys.stat().st_ino == inode
    keys.write_bytes(first + b'foreign provider record\n')
    run(pre + 'update_keys')
    assert keys.read_bytes() == first
    assert any(b'foreign provider record' in p.read_bytes() for p in (root/'backups').rglob('root-keys.saved'))
    run(pre + 'curl() { return 1; }; update_keys', False)
    assert keys.read_bytes() == first
    payload.write_text('invalid key material\n')
    run(pre + 'update_keys', False)
    assert keys.read_bytes() == first
    (root/'etc/sshd_config.d').mkdir(parents=True)
    config = root/'etc/sshd_config'
    config.write_text('# original\n')
    mock = pre + '''WORK=$(mktemp -d); STATE_DIR=$WORK;
cleanup() { rm -rf -- "$WORK"; }
die() { echo "ERROR: $*" >&2; exit 1; }
ensure_sshd_runtime_dir() { :; }
detect_ssh_ports() { SSH_CONTEXTS=('203.0.113.1 1234 192.0.2.1 22'); }
sshd() { [[ ${FAIL_SYNTAX:-0} == 0 ]]; }
validate_policy() { [[ ${FAIL_POLICY:-0} == 0 ]]; }
apply_ssh_runtime_config() { echo applied >> "$WORK/applied"; }
trap finish_update EXIT;
'''
    run(mock + 'harden_ssh')
    after = config.read_bytes()
    drop = root/'etc/sshd_config.d/00-proms-hardening.conf'
    assert b'PasswordAuthentication no' in drop.read_bytes()
    inode = config.stat().st_ino
    run(mock + 'harden_ssh; [[ ! -e "$WORK/applied" ]]')
    assert config.stat().st_ino == inode and config.read_bytes() == after
    config.write_text('# original\n')
    run(mock + 'FAIL_POLICY=1; harden_ssh', False)
    assert config.read_text() == '# original\n'
    run(mock + 'FAIL_SYNTAX=1; harden_ssh', False)
    assert config.read_text() == '# original\n'
    # Key-only mode must not enter hardening; OS/root checks isolated in this fixture.
    isolated = mapped(SOURCE).replace('. /etc/os-release', 'ID=ubuntu VERSION_ID=24.04 VERSION_CODENAME=noble')
    script.write_text(isolated)
    payload.write_text((root/'key.pub').read_text())
    run(pre + 'select_action() { NOPASS=0; }; harden_ssh() { exit 99; }; main')
    assert config.read_text() == '# original\n'
    # Real menu in a controlling PTY; only the operations/dependencies are mocked.
    calls = root/'menu-calls'
    menu = pre + f'''
update_keys() {{ echo keys >> {calls}; [[ ${{KEY_FAIL:-0}} == 0 ]]; }}
harden_ssh() {{ echo hardening >> {calls}; [[ ${{HARDEN_FAIL:-0}} == 0 ]]; }}
finish_update() {{ :; }}
command() {{ if [[ $1 == -v ]]; then return 0; fi; builtin command "$@"; }}
flock() {{ :; }}
'''
    def terminal(code, answer, expected=0):
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp('bash', ['bash', '-c', code])
        output = b''
        sent = False
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    output += chunk
                    if not sent and b'[1-3]:' in output:
                        os.write(fd, answer)
                        sent = True
            else:
                raise AssertionError('menu test timed out')
        finally:
            os.close(fd)
            if time.monotonic() >= deadline:
                os.kill(pid, 9)
            _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == expected, output.decode(errors='replace')
        return output

    for answer, expected_calls, status, extra in (
        (b'1\n', 'keys\n', 0, ''),
        (b'2\n', 'keys\nhardening\n', 0, ''),
        (b'2\n', 'keys\n', 1, 'KEY_FAIL=1; '),
        (b'3\n', '', 0, ''),
        (b'wrong\n\n3\n', '', 0, ''),
        (b'\x04', '', 1, ''),
        (b'\x03', '', 130, ''),
    ):
        calls.write_text('')
        terminal(menu + extra + 'main', answer, status)
        assert calls.read_text() == expected_calls
    calls.write_text('')
    terminal(menu + 'exec 0</dev/null; main', b'1\n')
    assert calls.read_text() == 'keys\n'  # controlling /dev/tty fallback
    calls.write_text('')
    # A new session has neither terminal stdin nor a controlling terminal.
    no_tty = subprocess.run(['bash', '-c', menu + 'main'], input='1\n',
                            capture_output=True, text=True, start_new_session=True)
    assert no_tty.returncode != 0 and 'Interactive terminal required' in no_tty.stderr
    assert calls.read_text() == ''
    for args, ok in (('--help', True), ('--nopass', False), ('--unknown', False)):
        run(menu + 'main ' + args, ok)
        assert calls.read_text() == ''
    run(pre + 'main --unknown', False)
    run(pre + 'main --help')
    run(pre + 'main --help --unknown', False)
    for socket, service, ok, reloads in ((1,1,True,1),(1,0,True,0),(0,1,True,1),(0,0,False,0)):
        log = root/'runtime-calls'
        log.write_text('')
        runtime = pre + f"""
SOCKET={socket}; SERVICE={service}
detect_ssh_ports() {{ return 0; }}
systemctl() {{
  if [[ $1 == reload && $2 == ssh.service ]]; then echo reload >> {log}; return 0; fi
  if [[ $* == 'is-active --quiet ssh.socket' ]]; then (( SOCKET ));
  elif [[ $* == 'is-active --quiet ssh.service' ]]; then (( SERVICE ));
  else exit 99; fi
}}
apply_ssh_runtime_config
"""
        run(runtime, ok)
        assert log.read_text().count('reload') == reloads
    config.write_text('# original\n')
    recovery = run(mock + 'NOPASS=1; apply_ssh_runtime_config() { return 1; }; harden_ssh', False)
    assert 'recovery incomplete' in recovery.stderr
    assert 'Keys were updated, but SSH hardening did not complete successfully' in recovery.stderr
    assert keys.read_bytes() == first
    assert config.read_text() == '# original\n'

print('PASS: key format/bootstrap compatibility, dedup, foreign removal, idempotence, failure preservation, interactive menu/EOF/interrupt/TTY, CLI and SSH rollback mocks')
