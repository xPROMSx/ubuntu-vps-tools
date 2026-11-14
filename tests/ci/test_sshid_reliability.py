"""Offline reliability checks. All system paths/commands are isolated."""
import errno
import os
import pathlib
import pty
import re
import select
import shlex
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = (ROOT / 'update-sshid').read_text()


class Reliability(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        source = SOURCE
        for path in ('/var/lib/dpkg', '/var/lib/apt/lists', '/var/cache/apt/archives',
                     '/etc/ssh', '/root/.ssh', '/root/backups'):
            target = self.root / path.lstrip('/')
            target.mkdir(parents=True, exist_ok=True)
            source = source.replace(path, str(target))
        source = source.replace('/tmp/update-sshid-diagnostics.', str(self.root / 'diagnostics.'))
        source = source.replace('/run/lock/vps-bootstrap-proms.lock', str(self.root / 'bootstrap.lock'))
        source = source.replace('[[ $EUID -eq 0 ]]', 'true')
        self.script = self.root / 'updater'
        self.script.write_text(source)
        self.prefix = f'''source {shlex.quote(str(self.script))}
WORK={shlex.quote(str(self.root / 'work'))}; mkdir -p "$WORK"; STATE_DIR=$WORK
prepare_sshd_runtime_dir() {{ :; }}
ensure_sshd_runtime_dir() {{ :; }}
systemctl() {{ printf 'active\\n'; }}
command() {{ if [[ "$*" == '-v cloud-init' ]]; then return 1; fi; builtin command "$@"; }}
'''
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('SSH_')}

    def run_shell(self, code, rc=0):
        result = subprocess.run(['bash', '-c', self.prefix + code], text=True,
                                capture_output=True, env=self.env, timeout=10)
        self.assertEqual(result.returncode, rc, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        error_file = self.root / 'work/ssh-python-error'
        if error_file.exists():
            self.assertNotIn('Traceback', error_file.read_text())
        return result

    def detector(self, listener, socket=False, effective='port 22', connection=''):
        (self.root / 'listeners').write_text(listener)
        (self.root / 'connections').write_text('')
        return f'''
sshd() {{ printf '%s\\n' {shlex.quote(effective)}; }}
systemctl() {{
  if [[ $1 == is-active ]]; then return {0 if socket else 1}; fi
  printf '22 (Stream)\\n'
}}
ss() {{ if [[ $2 == -ltnp ]]; then cat {self.root}/listeners;
        else cat {self.root}/connections; fi; }}
SSH_CONNECTION={shlex.quote(connection)}
'''

    def test_listener_fixtures(self):
        for owner, socket in (('sshd', False), ('systemd', True)):
            with self.subTest(owner=owner):
                listener = f'LISTEN 0 128 *:22 *:* users:(("{owner}",pid=1,fd=3))\n'
                self.run_shell(self.detector(listener, socket) + 'detect_ssh_ports_once')

    def test_missing_listener_temporary_status(self):
        self.run_shell(self.detector('') + 'detect_ssh_ports_once', 75)
        diagnostic = (self.root / 'work/ssh-diagnostic').read_text()
        self.assertIn('Ожидаемые порты: 22', diagnostic)
        self.assertIn('Найденные живые порты SSH:', diagnostic)

    def test_parser_and_connection_errors_not_temporary(self):
        listener = 'LISTEN 0 128 *:22 *:* users:(("sshd",pid=1,fd=3))\n'
        self.run_shell(self.detector(listener, effective='port bad') + 'detect_ssh_ports_once', 1)
        # Generate a documentation-only address in memory; never print it.
        import ipaddress
        address = str(ipaddress.IPv4Address((192 << 24) + (2 << 8) + 1))
        self.run_shell(self.detector(listener, connection=f'{address} 123 {address} 23')
                       + 'detect_ssh_ports_once', 1)

    def test_retries_success_and_exhaustion(self):
        for success, expected, pauses in ((3, 0, 2), (99, 1, 4)):
            result = self.run_shell(f'''
n=0; naps=0
detect_ssh_ports_once() {{ n=$((n+1)); : > "$STATE_DIR/ssh-python-error"; (( n >= {success} )) && return 0; return 75; }}
sleep() {{ naps=$((naps+1)); }}
rc=0; detect_ssh_ports || rc=$?
[[ $rc == {expected} && $n == {3 if expected == 0 else 5} && $naps == {pauses} ]]
''')
            if expected:
                self.assertIn('Диагностика сохранена:', result.stderr)
                self.assertTrue(list(self.root.glob('diagnostics.*')))
        self.run_shell('''
n=0
detect_ssh_ports_once() { n=$((n+1)); return 1; }
sleep() { exit 98; }
detect_ssh_ports && exit 99
[[ $n == 1 ]]
''')

    def test_ss_stderr_suppressed_on_success_and_shown_on_failure(self):
        listener = 'LISTEN 0 128 *:22 *:* users:(("sshd",pid=1,fd=3))\n'
        code = self.detector(listener) + '''
ss() {
  printf 'Failed to open cgroup2 by ID\\n' >&2
  if [[ $2 == -ltnp ]]; then cat ''' + str(self.root) + '''/listeners; fi
}
detect_ssh_ports
'''
        self.assertNotIn('cgroup2', self.run_shell(code).stderr)
        failed = self.run_shell(self.detector('') + '''
ss() { printf 'Failed to open cgroup2 by ID\\n' >&2; return 2; }
detect_ssh_ports
''', 1)
        self.assertIn('cgroup2', failed.stderr)
        self.assertIn('ss -ltnp: 2', failed.stderr)
        self.assertIn('ss -tnpe: 2', failed.stderr)

    def test_busy_cloud_and_transitional_unit(self):
        self.run_shell('system_busy')  # Absent cloud-init, no locks, settled units.
        self.run_shell('''
command() { builtin command "$@"; }
cloud-init() { printf '{"status":"running"}'; }
system_busy
''', 75)
        for state in ('activating', 'deactivating', 'reloading'):
            self.run_shell(f'systemctl() {{ printf "{state}\\n"; }}; system_busy', 75)
        inactive = self.run_shell('systemctl() { printf "inactive\\n"; }; system_busy', 75)
        self.assertIn('SSH-юниты не активны', inactive.stdout)
        self.run_shell('systemctl() { printf "unrecognized\\n"; }; system_busy', 1)
        self.run_shell('systemctl() { return 1; }; system_busy', 1)
        for state in ('done', 'disabled', 'error'):
            self.run_shell('command() { builtin command "$@"; }; '
                           + f'''cloud-init() {{ printf '{{"status":"{state}"}}'; }}; system_busy''')
        self.run_shell('''
systemctl() {
  if [[ $2 == ssh.socket ]]; then
    [[ $4 != LoadState ]] || printf 'not-found\\n'
    return 1
  fi
  printf 'active\\n'
}
system_busy
''')

    def test_key_only_main_skips_wait_and_reports(self):
        result = self.run_shell('''
select_action() { NOPASS=0; }
system_busy() { exit 99; }
update_keys() { KEYS_CHANGED=1; }
sshd() { printf 'passwordauthentication yes\\n'; }
main
''')
        self.assertIn('Ключи SSH: обновлены', result.stderr)
        self.assertIn('Вход по паролю: НЕ ОТКЛЮЧЁН', result.stderr)
        self.assertIn('Для отключения выберите пункт 2', result.stderr)

    def test_real_fcntl_lock(self):
        path = self.root / 'var/lib/dpkg/lock'
        path.touch()
        code = 'import fcntl,sys; f=open(sys.argv[1],"r+b"); fcntl.lockf(f,fcntl.LOCK_EX); print("ready",flush=True); sys.stdin.read()'
        holder = subprocess.Popen(['python3', '-c', code, str(path)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'ready')
            self.run_shell('system_busy', 75)
        finally:
            holder.communicate('', timeout=5)
        self.run_shell('system_busy')
        self.assertFalse((path.parent / 'lock-frontend').exists())

    def test_main_preflight_and_key_before_hardening(self):
        for active, expected in ((False, 1), (True, 0)):
            calls = self.root / 'calls'
            calls.write_text('')
            self.run_shell(f'''
select_action() {{ NOPASS=1; }}
SYSTEM_WAIT_SECONDS=0
command() {{ [[ "$*" != '-v cloud-init' ]]; }}
systemctl() {{ printf '{"active" if active else "inactive"}\\n'; }}
flock() {{ :; }}
update_keys() {{ echo keys >> {calls}; KEYS_CHANGED=1; }}
harden_ssh() {{ echo hardening >> {calls}; CONFIG_CHANGED=1; }}
sshd() {{ printf 'passwordauthentication no\\n'; }}
main
''', expected)
            self.assertEqual(calls.read_text(), 'keys\nhardening\n' if active else '')

    def test_inactive_units_return_before_deadline(self):
        calls = self.root / 'calls'
        result = self.run_shell(f'''
select_action() {{ NOPASS=1; }}
command() {{ [[ "$*" != '-v cloud-init' ]]; }}
echo inactive > "$STATE_DIR/unit-state"
systemctl() {{ cat "$STATE_DIR/unit-state"; }}
sleep() {{
  [[ ! -e {calls} ]]
  echo active > "$STATE_DIR/unit-state"
}}
flock() {{ :; }}
update_keys() {{ echo keys >> {calls}; KEYS_CHANGED=1; }}
harden_ssh() {{ echo hardening >> {calls}; CONFIG_CHANGED=1; }}
sshd() {{ printf 'passwordauthentication no\\n'; }}
main
''')
        self.assertEqual(calls.read_text(), 'keys\nhardening\n')
        self.assertIn('SSH-юниты не активны', result.stderr)
        self.assertIn('Вход по паролю: ОТКЛЮЧЁН', result.stderr)

    def test_inactive_units_timeout_preserves_files(self):
        config = self.root / 'etc/ssh/sshd_config'
        keys = self.root / 'root/.ssh/authorized_keys'
        config.write_text('# original config\n')
        keys.write_text('# original keys\n')
        result = self.run_shell('''
select_action() { NOPASS=1; }
SYSTEM_WAIT_SECONDS=0
command() { [[ "$*" != '-v cloud-init' ]]; }
systemctl() { printf 'inactive\\n'; }
update_keys() { exit 98; }
harden_ssh() { exit 99; }
sshd() { printf 'passwordauthentication yes\\n'; }
main
''', 1)
        self.assertEqual(config.read_text(), '# original config\n')
        self.assertEqual(keys.read_text(), '# original keys\n')
        for text in ('SSH не запущен', 'настройка входа по паролю осталась прежней',
                     'systemctl status ssh', 'systemctl status ssh.socket',
                     'Ключи SSH: не менялись', 'Конфигурация SSH: не менялась'):
            self.assertIn(text, result.stderr)

    def test_runtime_activity_retry_does_not_repeat_reload(self):
        self.run_shell('''
checks=0; reloads=0
systemctl() {
  if [[ $1 == reload ]]; then reloads=$((reloads+1)); return 0; fi
  [[ $3 != ssh.socket ]] || return 1
  checks=$((checks+1))
  (( checks == 1 || checks >= 4 ))
}
sleep() { :; }
detect_ssh_ports() { :; }
apply_ssh_runtime_config
[[ $reloads == 1 ]]
''')

    def test_finish_restoration_and_retained_recovery(self):
        config = self.root / 'etc/ssh/sshd_config'
        drop = config.parent / 'sshd_config.d/00-proms-hardening.conf'
        drop.parent.mkdir(exist_ok=True)
        work = self.root / 'work'
        for runtime_ok in (True, False):
            work.mkdir(exist_ok=True)
            (work / 'ssh-main').write_text('# original\n')
            config.write_text('# changed\n')
            drop.write_text('# changed\n')
            result = self.run_shell(f'''
SUMMARY_ENABLED=1; NOPASS=1; TX_PENDING=1; KEYS_CHANGED=1
sshd() {{ [[ $1 == -t ]] || printf 'passwordauthentication yes\\n'; }}
apply_ssh_runtime_config() {{ return {0 if runtime_ok else 1}; }}
trap finish_update EXIT
exit 1
''', 1)
            self.assertEqual(config.read_text(), '# original\n')
            self.assertFalse(drop.exists())
            self.assertEqual(work.exists(), not runtime_ok)
            final = result.stderr.splitlines()[-1]
            self.assertIn('восстановлены и проверены' if runtime_ok else 'откат НЕ удался', final)

    def test_wait_release_timeout_and_interrupt(self):
        self.run_shell('''
count=0
system_busy() { count=$(cat "$STATE_DIR/count" 2>/dev/null || printf 0); echo $((count+1)) > "$STATE_DIR/count"; (( count >= 2 )) && return 0; return 75; }
sleep() { :; }
wait_system_idle
''')
        timeout = self.run_shell('''
SUMMARY_ENABLED=1; NOPASS=1; SYSTEM_WAIT_SECONDS=0
sshd() { printf 'passwordauthentication yes\\n'; }
trap finish_update EXIT
system_busy() { return 75; }
wait_system_idle
''', 1)
        self.assertIn('Не дождались', timeout.stderr)
        self.assertIn('Ключи SSH: не менялись', timeout.stderr)
        interrupted = self.run_shell('''
SUMMARY_ENABLED=1; NOPASS=1
sshd() { printf 'passwordauthentication yes\\n'; }
trap finish_update EXIT; trap 'exit 130' INT
system_busy() { return 75; }
sleep() { kill -INT $$; }
wait_system_idle
''', 130)
        self.assertNotIn('Не дождались', interrupted.stderr)
        self.assertIn('Ключи SSH: не менялись', interrupted.stderr)

    def test_fresh_summary_scenarios(self):
        for flags, rc, restored, fragment in (
            ('NOPASS=1; KEYS_CHANGED=1; CONFIG_CHANGED=1', 0, 0, 'применены и проверены'),
            ('NOPASS=1; KEYS_CHANGED=1', 1, 0, 'не менялась'),
            ('NOPASS=1; KEYS_CHANGED=1; TX_PENDING=1', 1, 1, 'восстановлены и проверены'),
            ('NOPASS=1; TX_PENDING=1; TX_KEEP=1', 1, 0, 'откат НЕ удался'),
            ('NOPASS=0; KEYS_CHANGED=1', 0, 0, 'не менялась'),
        ):
            result = self.run_shell(flags + f'''
sshd() {{ printf 'passwordauthentication no\\n'; }}
report_update {rc} {restored}
''')
            lines = result.stderr.splitlines()
            self.assertIn('Ключи SSH:', lines[-3])
            self.assertIn('Вход по паролю: ОТКЛЮЧЁН', lines[-2])
            self.assertIn(fragment, lines[-1])
            self.assertNotIn('\x1b', result.stderr)
            if 'NOPASS=0' in flags:
                self.assertIn('Для отключения выберите пункт 2', result.stderr)
        unknown = self.run_shell('prepare_sshd_runtime_dir() { return 1; }; report_update 1 0')
        self.assertIn('Вход по паролю: неизвестно', unknown.stderr)
        self.run_shell('sshd() { return 255; }; [[ $(disk_password_status) == неизвестно ]]')
        self.run_shell('''
sshd() { printf 'passwordauthentication no\\n'; }
root_ssh_config() { printf 'passwordauthentication yes\\n'; }
[[ $(disk_password_status) == 'НЕ ОТКЛЮЧЁН' ]]
''')

    def test_color_and_no_color_in_terminal(self):
        for no_color in (False, True):
            pid, fd = pty.fork()
            if pid == 0:
                code = self.prefix + ('NO_COLOR=; ' if no_color else 'unset NO_COLOR; ') + 'message КРИТИЧНО fixture'
                os.execve('/bin/bash', ['bash', '-c', code], self.env)
            output = b''
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if select.select([fd], [], [], 0.1)[0]:
                        try:
                            data = os.read(fd, 4096)
                        except OSError as error:
                            if error.errno == errno.EIO:
                                break
                            raise
                        if not data:
                            break
                        output += data
                else:
                    os.kill(pid, 9)
                    self.fail('terminal timeout')
            finally:
                os.close(fd)
                os.waitpid(pid, 0)
            self.assertEqual(b'\x1b[1;31m' in output, not no_color)



if __name__ == '__main__':
    unittest.main()
