"""Fast argument/validation tests: no bootstrap execution or system changes."""
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

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vps-bootstrap-ubuntu24.sh"
SOURCE = SCRIPT.read_text()


def run(code, success=True, env=None):
    result = subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                            env={**os.environ, **(env or {})}, cwd=ROOT)
    assert (result.returncode == 0) == success, result.stderr
    assert "CI_SECRET_CANARY" not in result.stdout + result.stderr
    return result


for option in ("--help", "-h"):
    assert "Supported: Ubuntu Server 24.04 LTS and Ubuntu Server 26.04 LTS" in run(
        f"bash -x {SCRIPT} {option}", env={"UBUNTU_PRO_TOKEN": "CI_SECRET_CANARY"}).stdout
for args in ("--unknown", "--ssh-port", "--ssh-port ''"):
    run(f"bash {SCRIPT} {args}", success=False)
# Sourcing defines functions only; disable the EXIT cleanup before unit calls.
prefix = f"source {SCRIPT}; trap - EXIT; "
for ports in ("auto", "ssh", "22", "1,65535", "022"):
    run(prefix + f"parse_args --ssh-port {ports}; validate_inputs")
for ports in ("0", "65536", "22,", "-1", "bad"):
    run(prefix + f"parse_args --ssh-port '{ports}'; validate_inputs", success=False)
for value in ("00:00", "23:59", "04:38"):
    run(prefix + f"AUTO_REBOOT_TIME={value}; validate_inputs")
for value in ("24:00", "1:00", "00:60"):
    run(prefix + f"AUTO_REBOOT_TIME={value}; validate_inputs", success=False)
for tz in ("../etc/passwd", "/etc/passwd", "Not/AZone"):
    run(prefix + f"TIMEZONE='{tz}'; validate_inputs", success=False)
run(prefix + "IGNORE_IPS=$'127.0.0.1\\nother'; validate_inputs", success=False)
run(prefix + "parse_args --no-upgrade --autoremove --skip-dns; "
    "[[ $RUN_UPGRADE == 0 && $RUN_AUTOREMOVE == 1 && $CONFIGURE_DNS == 0 ]]")
run(prefix + "parse_args --noswap; [[ $CONFIGURE_SWAP == 0 ]]")
assert "--noswap" in run(f"bash {SCRIPT} --help").stdout
# Isolate OS validation against fixture os-release files, not runner's identity.
check = re.search(r"^check_os\(\) \{\n.*?^\}", SOURCE, re.M | re.S).group()
with tempfile.TemporaryDirectory() as directory:
    fixture = pathlib.Path(directory) / "os-release"
    isolated = check.replace("/etc/os-release", str(fixture))
    for version, codename, supported in (("24.04", "noble", True), ("26.04", "resolute", True),
                                         ("22.04", "jammy", False), ("26.04", "noble", False)):
        fixture.write_text(f"ID=ubuntu\nVERSION_ID={version}\nVERSION_CODENAME={codename}\n")
        run(prefix + isolated + "\ncheck_os", success=supported)
assert not re.search(r"^\s*set\s+-(?:[^\s]*x)", SOURCE, re.M)
print("PASS: help, argument values, OS compatibility and trace/token canary")

# Isolated swap simulations: redirect only this helper's paths to a disposable
# directory. No real mkswap/swapon or host configuration writes are performed.
swap = re.search(r"^configure_swap\(\) \{\n.*?^\}", SOURCE, re.M | re.S).group()
cases = (
    ("create", "", "ok", "1 GiB active; persistent"),
    ("xfs", "FS=xfs", "ok", "1 GiB active; persistent"),
    ("active", "ACTIVE=/dev/zram0", "info", "existing swap preserved"),
    ("noswap", "parse_args --noswap", "info", "skipped by --noswap"),
    ("space", "FREE=4294967295", "warn", "less than 4 GiB free"),
    ("filesystem", "FS=btrfs", "warn", "unsupported filesystem"),
    ("mkswap", "FAIL_MKSWAP=1", "warn", "creation/activation failed"),
    ("swapon", "FAIL_SWAPON=1", "warn", "creation/activation failed"),
    ("inactive", 'printf foreign > "$SWAP_TEST_FILE"', "warn", "existing inactive"),
    ("symlink", 'ln -s "$SWAP_TEST_DIR/foreign" "$SWAP_TEST_FILE"', "warn", "existing inactive"),
    ("fstab", "printf '%s none swap sw 0 0\\n' \"$SWAP_TEST_FILE\" >> \"$SWAP_TEST_FSTAB\"",
     "warn", "fstab entry"),
    ("persistence", "FAIL_BACKUP=1", "warn", "persistence failed"),
    ("active-unlink", "BLOCK_ACTIVE_UNLINK=1", "ok", "1 GiB active; persistent"),
)
with tempfile.TemporaryDirectory() as directory:
    for name, setup, state, detail in cases:
        path = pathlib.Path(directory) / name
        path.mkdir()
        helper = swap.replace("/swapfile", str(path / "swapfile"))
        helper = helper.replace("/etc/fstab", str(path / "fstab"))
        helper = helper.replace("/.swapfile-bootstrap.", str(path / ".swapfile-bootstrap."))
        helper = helper.replace("/etc/.fstab-swap.", str(path / ".fstab-swap."))
        mocks = r'''
FS=ext4 FREE=4294967296 ACTIVE=''
printf '# preserved fstab\n' > "$SWAP_TEST_FSTAB"
printf foreign > "$SWAP_TEST_DIR/foreign"
swapon() {
  if [[ "$*" == '--show=NAME --noheadings' ]]; then printf '%s' "$ACTIVE"; return 0; fi
  printf 'activate\n' >> "$SWAP_TEST_DIR/calls"
  [[ ${FAIL_SWAPON:-0} == 0 ]] || return 1
  ACTIVE=$1
}
findmnt() { printf '%s\n' "$FS"; }
df() { printf 'Avail\n%s\n' "$FREE"; }
fallocate() { printf allocated > "${@: -1}"; }
chown() { :; }
stat() {
  if [[ ${2:-} == '%s:%u:%g:%a' ]]; then printf '1073741824:0:0:600\n';
  else command stat "$@"; fi
}
rm() {
  if [[ ${BLOCK_ACTIVE_UNLINK:-0} == 1 && -n "$ACTIVE" ]]; then
    for file in "$@"; do
      if [[ -e "$file" && "$file" -ef "$ACTIVE" ]]; then return 1; fi
    done
  fi
  command rm "$@"
}
mkswap() { printf 'mkswap\n' >> "$SWAP_TEST_DIR/calls"; [[ ${FAIL_MKSWAP:-0} == 0 ]]; }
backup_file() { [[ ${FAIL_BACKUP:-0} == 0 ]] && cp "$1" "$SWAP_TEST_DIR/backup"; }
'''
        result = run(prefix + helper + mocks + setup + r'''
cp "$SWAP_TEST_FSTAB" "$SWAP_TEST_DIR/before"
configure_swap
[[ ${#FAILED_CHECKS[@]} == 0 ]]
[[ ${SUMMARY_STATE[swap]} == "$SWAP_TEST_STATE" ]]
[[ ${SUMMARY_DETAIL[swap]} == *"$SWAP_TEST_DETAIL"* ]]
final_summary
[[ $(cat "$SWAP_TEST_DIR/foreign") == foreign ]]
if [[ ${SUMMARY_STATE[swap]} == ok ]]; then
  [[ "$ACTIVE" == "$SWAP_TEST_FILE" ]]
  [[ $(command stat -c %h "$SWAP_TEST_FILE") == 1 ]]
  [[ $(grep -Fc "$SWAP_TEST_FILE none swap sw 0 0" "$SWAP_TEST_FSTAB") == 1 ]]
  cp "$SWAP_TEST_FSTAB" "$SWAP_TEST_DIR/after"
  inode=$(command stat -c %i "$SWAP_TEST_FILE")
  configure_swap
  [[ ${SUMMARY_STATE[swap]} == info && $(command stat -c %i "$SWAP_TEST_FILE") == "$inode" ]]
  cmp "$SWAP_TEST_DIR/after" "$SWAP_TEST_FSTAB"
  [[ $(grep -c activate "$SWAP_TEST_DIR/calls") == 1 ]]
else
  cmp "$SWAP_TEST_DIR/before" "$SWAP_TEST_FSTAB"
  [[ ${#WARNINGS[@]} -le 1 ]]
  case "$SWAP_TEST_CASE" in
    active|noswap|space|filesystem|inactive|symlink|fstab) [[ ! -e "$SWAP_TEST_DIR/calls" ]] ;;
  esac
  if [[ "$SWAP_TEST_CASE" == inactive ]]; then [[ $(cat "$SWAP_TEST_FILE") == foreign ]];
  elif [[ "$SWAP_TEST_CASE" == symlink ]]; then [[ -L "$SWAP_TEST_FILE" ]];
  elif [[ "$SWAP_TEST_CASE" == persistence ]]; then [[ -f "$SWAP_TEST_FILE" && "$ACTIVE" == "$SWAP_TEST_FILE" ]];
  else [[ ! -e "$SWAP_TEST_FILE" ]]; fi
fi
[[ -z $(find "$SWAP_TEST_DIR" -name '.swapfile-bootstrap.*' -o -name '.fstab-swap.*') ]]
''', env={"SWAP_TEST_DIR": str(path), "SWAP_TEST_FILE": str(path / "swapfile"),
          "SWAP_TEST_FSTAB": str(path / "fstab"), "SWAP_TEST_STATE": state,
          "SWAP_TEST_DETAIL": detail, "SWAP_TEST_CASE": name})
        token = {"ok": "[✓]", "info": "[i]", "warn": "[!]"}[state]
        assert re.search(re.escape(token) + r" Swap\s+.*" + re.escape(detail), result.stdout)
        assert "RESULT: FAILED" not in result.stdout
        print(f"PASS: swap SIMULATION {name}; summary and non-fatal result")

# Exercise the actual guest log assertions without VM commands or raw-log output.
guest = (ROOT / "tests/ci/guest.sh").read_text()
assertions = re.search(r'python3 - "/root/\$name.log" <<\'PY\'\n(.*?)\nPY', guest, re.S).group(1)
with tempfile.TemporaryDirectory() as directory:
    fixture = pathlib.Path(directory) / "initial.log"
    clean = 'RESULT: SUCCESS\nFull upgrade and autoclean completed\n[✓] Swap  1 GiB active; persistent\n'
    samples = (
        (clean, True, 'PASS: bootstrap result'),
        (clean.replace('RESULT: SUCCESS\n', ''), False, 'success RESULT assertion'),
        (clean + 'FAILED CHECK: redacted fixture\n', False, 'absence of FAILED CHECK assertion'),
        (clean.replace('Full upgrade and autoclean completed', ''), False, 'package upgrade completion assertion'),
        (clean.replace('[✓] Swap  1 GiB active; persistent',
                       '[!] Swap  skipped; creation/activation failed'), False, 'swap initial provisioning assertion'),
        (clean + 'WARN: PRIVATE_DIAG_CANARY\n', False, 'warning allowlist assertion'),
        (clean.replace('[✓] Swap  1 GiB active; persistent',
                       '[!] Swap  PRIVATE_DIAG_CANARY'), False, 'swap initial provisioning assertion'),
    )
    for content, success, expected in samples:
        fixture.write_text(content)
        isolated = ('import subprocess,sys,types; '
                    'subprocess.run=lambda *a,**kw: types.SimpleNamespace(returncode=0,stdout="PRIVATE_DIAG_CANARY"); '
                    f'sys.argv=["fixture",{str(fixture)!r}]; exec({assertions!r})')
        output = run("python3 -c " + shlex.quote(isolated), success=success).stdout
        assert expected in output and 'PRIVATE_DIAG_CANARY' not in output
    print('PASS: guest assertion labels, strict swap success requirement and diagnostic redaction')

for args in ('--reboot', '--nopro', '--reboot --nopro', '--nopro --reboot'):
    run(prefix + f'parse_args {args}; '
        f'[[ $RUN_REBOOT == {int("--reboot" in args)} && $CONFIGURE_PRO == {int("--nopro" not in args)} ]]')
help_text = run(f'bash {SCRIPT} --help').stdout
assert '--reboot' in help_text and '--nopro' in help_text
run(prefix + 'parse_args --reboot --nopro --noswap --no-upgrade --autoremove --skip-dns; '
    '[[ $RUN_REBOOT == 1 && $CONFIGURE_PRO == 0 && $CONFIGURE_SWAP == 0 && '
    '$RUN_UPGRADE == 0 && $RUN_AUTOREMOVE == 1 && $CONFIGURE_DNS == 0 ]]')
with tempfile.TemporaryDirectory() as directory:
    state = pathlib.Path(directory) / 'pro-state'
    state.write_text('existing subscription')
    run(prefix + 'pro() { printf changed > "$PRO_TEST_STATE"; return 99; }; '
        'read() { exit 99; }; parse_args --nopro; configure_ubuntu_pro; '
        '[[ ${SUMMARY_STATE[pro]} == info && ${SUMMARY_DETAIL[pro]} == "skipped by --nopro" && '
        '${#WARNINGS[@]} == 0 && ${#FAILED_CHECKS[@]} == 0 && -z ${UBUNTU_PRO_TOKEN+x} ]]; unset -f read; final_summary',
        env={'UBUNTU_PRO_TOKEN': 'CI_SECRET_CANARY', 'PRO_TEST_STATE': str(state)})
    assert state.read_text() == 'existing subscription'
print('PASS: reboot/nopro arguments and non-interactive Pro preservation')

# Execute the real EXIT handler with only mock cleanup/systemd operations.
finish = re.search(r'^finish\(\) \{\n.*?^\}', SOURCE, re.M | re.S).group()
cases = (
    ('default', 'RUN_REBOOT=0', 0, False),
    ('success', '', 0, True),
    ('warnings', 'WARNINGS=(optional)', 0, True),
    ('unfinished', 'BOOTSTRAP_COMPLETED=0', 0, False),
    ('failed-check', 'FAILED_CHECKS=(failed)', 0, False),
    ('error', 'set +e; false; set -e', 0, False),
    ('exit-failure', 'exit 1', 1, False),
    ('sigint', 'kill -s INT $$', 130, False),
    ('sigterm', 'kill -s TERM $$', 143, False),
    ('cleanup-failure', 'FAIL_CLEANUP=1', 1, False),
    ('cleanup-marker', 'touch "$STATE_DIR/ipv6-route-owned"', 0, False),
    ('remove-failure', 'FAIL_REMOVE=1', 1, False),
    ('rollback-failure', 'TX_SERVICE=dns; FAIL_ROLLBACK=1', 1, False),
    ('pending-transaction', 'TX_SERVICE=dns', 0, False),
    ('pending-files', 'TX_FILES=(config)', 0, False),
    ('incomplete-recovery', 'TX_RECOVERY_INCOMPLETE=1', 0, False),
    ('not-required', 'rm "$REBOOT_TEST_DIR/required"', 0, False),
    ('schedule-failure', 'FAIL_SCHEDULE=1', 1, True),
)
with tempfile.TemporaryDirectory() as directory:
    for name, setup, rc, schedules in cases:
        path = pathlib.Path(directory) / name
        path.mkdir()
        (path / 'required').touch()
        handler = finish.replace('/run/reboot-required', str(path / 'required'))
        mocks = r'''
RUN_REBOOT=1 BOOTSTRAP_COMPLETED=1
STATE_DIR="$REBOOT_TEST_DIR/state"
mkdir -p "$STATE_DIR"
cleanup_ipv6_guard() { echo cleanup >> "$REBOOT_TEST_DIR/calls"; [[ ${FAIL_CLEANUP:-0} == 0 ]]; }
rollback_transaction() {
  echo rollback >> "$REBOOT_TEST_DIR/calls"
  [[ ${FAIL_ROLLBACK:-0} == 0 ]] || return 1
  TX_SERVICE=''; TX_FILES=()
}
rm() { [[ ${FAIL_REMOVE:-0} == 0 ]] || return 1; command rm "$@"; }
systemctl() { [[ "$*" == 'is-active --quiet vps-bootstrap-reboot.timer' ]]; [[ -f "$REBOOT_TEST_DIR/timer" ]]; }
systemd-run() {
  [[ "$*" == '--unit=vps-bootstrap-reboot --on-active=30s --timer-property=AccuracySec=1s /usr/bin/systemctl reboot' ]] || exit 99
  [[ ! -e "$STATE_DIR" ]] || exit 99
  echo schedule >> "$REBOOT_TEST_DIR/calls"
  [[ ${FAIL_SCHEDULE:-0} == 0 ]] || return 1
  touch "$REBOOT_TEST_DIR/timer"
}
'''
        code = prefix + handler + mocks + 'trap finish EXIT; ' + (setup or ':') + '; :'
        result = run(code, success=(rc == 0), env={'REBOOT_TEST_DIR': str(path)})
        assert result.returncode == rc
        calls = (path / 'calls').read_text().splitlines()
        assert ('schedule' in calls) == schedules
        if name == 'schedule-failure':
            assert 'Could not schedule' in result.stderr and 'scheduled in 30 seconds' not in result.stdout
        elif schedules:
            assert 'Automatic reboot scheduled in 30 seconds.' in result.stdout
            again = run(code, env={'REBOOT_TEST_DIR': str(path)})
            assert 'existing timer preserved' in again.stdout
            assert (path / 'calls').read_text().splitlines().count('schedule') == 1
        elif name == 'not-required':
            assert 'Automatic reboot skipped: not required.' in result.stdout
        print(f'PASS: reboot MOCK {name}; cleanup gating and timer behavior')

# Exercise real main/APT/Pro logic with isolated paths and mocked host operations.
main = re.search(r'^main\(\) \{\n.*?^\}', SOURCE, re.M | re.S).group()
readme = (ROOT / 'README.md').read_text()
options = set(re.findall(r'--[a-z][a-z-]*', help_text.split('Environment overrides:')[0]))
assert options == set(re.findall(r'--[a-z][a-z-]*', readme.split('## Bootstrap usage')[1].split('## Standalone')[0]))
assert '`-h`' in readme or '`-h, --help`' in readme
for variable in re.findall(r'^  ([A-Z_]+)=', help_text, re.M):
    assert f'`{variable}`' in readme
print('PASS: README/help CLI and environment override coverage')

# Real TTY selection and main dispatch; every installation step is a mock.
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    calls = path / 'calls'
    lock = path / 'lock'
    isolated = main.replace('/run/lock/vps-bootstrap-proms.lock', str(lock))
    isolated = isolated.replace('/run/vps-bootstrap-proms.XXXXXX', directory + '/state.XXXXXX')
    mocks = ''.join(f'{name}() {{ :; }}; ' for name in '''
        validate_inputs check_os flock provision_proms_ssh_keys migrate_legacy_backups
        check_root_authorized_keys configure_swap initial_full_upgrade apt_install_base_packages
        configure_bootstrap_ipv4_fallback set_timezone configure_ubuntu_pro apt_update apt_cleanup
        check_systemd_manager configure_ssh configure_resolved configure_sysctl
        configure_unattended_upgrades configure_ufw configure_fail2ban
    '''.split())
    code = prefix + isolated + '\n' + mocks + f'''
require_root() {{ printf 'start %s %s\n' "$CONFIGURE_PRO" "$RUN_REBOOT" >> {calls}; }}
final_report() {{ printf 'report\n' >> {calls}; }}
unset NO_COLOR; TERM=xterm-256color
'''

    def terminal(command, answer=b'', token_answer=b'\n'):
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp('bash', ['bash', '-c', command])
        output = b''
        sent = False
        token_sent = False
        finished = False
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            finished = True
                            break
                        raise
                    if not chunk:
                        finished = True
                        break
                    output += chunk
                    assert b'CI_SECRET_CANARY' not in output
                    if not token_sent and b'Ubuntu Pro token (Enter to skip):' in output:
                        assert not lock.exists()  # Input precedes lock/installation.
                        os.write(fd, token_answer)
                        token_sent = True
                    if not sent and b'[1-5]:' in output:
                        # No preflight, lock or installation before selection.
                        assert calls.read_text() == '' and not lock.exists()
                        os.write(fd, answer)
                        sent = True
            else:
                raise AssertionError(f'bootstrap menu test timed out: {output!r}')
        finally:
            os.close(fd)
            if not finished:
                os.kill(pid, 9)
            _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0, output.decode(errors='replace')
        return output.decode()

    for answer, flags, retries in (
        (b'1\n', '1 0', 0), (b'2\n', '1 1', 0),
        (b'3\n', '0 0', 0), (b'4\n', '0 1', 0),
        (b'5\n', None, 0), (b'\x04', None, 0),
        (b'wrong\n0\n6\n\n4\n', '0 1', 4),
    ):
        calls.write_text('')
        lock.unlink(missing_ok=True)
        output = terminal(code + 'main; printf "completed=%s\\n" "$BOOTSTRAP_COMPLETED"', answer)
        assert calls.read_text() == (f'start {flags}\nreport\n' if flags else '')
        assert lock.exists() == bool(flags)
        assert f'completed={int(bool(flags))}' in output
        assert output.count('UBUNTU VPS TOOLS') == 1
        assert output.count('[1-5]:') == retries + 1
        assert output.count('Неверный выбор') == retries
        assert '\033[1;36m' in output and '\033[1;37m' in output and '\033[2m' in output
    for args in ('--reboot', '--nopro', '--nopro --reboot', '--noswap', '--no-upgrade',
                 '--autoremove', '--skip-dns', '--ssh-port 2222', '--help', '-h'):
        calls.write_text('')
        lock.unlink(missing_ok=True)
        output = terminal(code + f'main {args}')
        flags = f'{int("--nopro" not in args)} {int("--reboot" in args)}'
        assert calls.read_text() == ('' if args in ('--help', '-h') else f'start {flags}\nreport\n')
        assert 'UBUNTU VPS TOOLS' not in output and '[1-5]:' not in output
        if args in ('--help', '-h'):
            assert 'Usage:' in output and not lock.exists()
    for invoke in ('main', 'main </dev/null'):
        calls.write_text('')
        output = run(code + invoke).stdout
        assert calls.read_text() == 'start 1 0\nreport\n'
        assert 'UBUNTU VPS TOOLS' not in output and '[1-5]:' not in output
    calls.write_text('')
    output = terminal(code + 'main </dev/null')
    assert calls.read_text() == 'start 1 0\nreport\n' and '[1-5]:' not in output
    calls.write_text('')
    lock.unlink(missing_ok=True)
    terminal(code + f'main > {path / "redirected"}')
    assert calls.read_text() == 'start 1 0\nreport\n'
    assert '[1-5]:' not in (path / 'redirected').read_text()
print('PASS: bootstrap menu 1-5, flags, invalid input/EOF, single run, CLI/help bypass and non-TTY dispatch MOCKS')

with tempfile.TemporaryDirectory() as directory:
    isolated = main.replace('/run/lock/vps-bootstrap-proms.lock', directory + '/lock')
    isolated = isolated.replace('/run/vps-bootstrap-proms.XXXXXX', directory + '/state.XXXXXX')
    mocks = r'''
record() { printf '%s\n' "$*" >> "$APT_TEST_DIR/calls"; }
require_root() { record root; }
validate_inputs() { record inputs; }
check_os() { record os; }
provision_proms_ssh_keys() { record keys; }
migrate_legacy_backups() { record backups; }
check_root_authorized_keys() { record key-check; }
configure_swap() { record swap; }
configure_bootstrap_ipv4_fallback() { record network; }
set_timezone() { record timezone; }
check_systemd_manager() { record systemd; }
configure_ssh() { record ssh; }
configure_resolved() { record dns; }
configure_sysctl() { record sysctl; }
configure_unattended_upgrades() { record unattended; }
configure_ufw() { record ufw; }
configure_fail2ban() { record fail2ban; }
final_report() { record report; [[ ${#FAILED_CHECKS[@]} == 0 ]]; }
dpkg-query() { printf 'install ok installed\n'; }
apt-mark() { record "mark $*"; }
apt-get() {
  record "apt $*"
  [[ -z ${FAIL_APT:-} || " $* " != *" $FAIL_APT "* ]]
}
pro() {
  record "pro $*"
  if [[ $1 == enable ]]; then
    [[ ${PRO_MODE:-} != enable-failure && ! ( ${PRO_MODE:-} == partial-failure && $3 == esm-apps ) ]] || return 1
    [[ ${PRO_MODE:-} == unconfirmed ]] || touch "$APT_TEST_DIR/$3"
    return 0
  fi
  [[ $1 == status ]] || return 99
  if [[ ${PRO_MODE:-} == verify-failure && -e "$APT_TEST_DIR/esm-infra" ]]; then return 1; fi
  local infra=disabled apps=disabled live=unavailable attached=true
  case ${PRO_MODE:-} in
    existing) infra=enabled apps=enabled ;;
    livepatch-only) infra=enabled apps=enabled live=disabled ;;
    infra-only) apps=unavailable ;;
    apps-only) infra=unavailable ;;
    unattached) attached=false ;;
  esac
  [[ ! -e "$APT_TEST_DIR/esm-infra" ]] || infra=enabled
  [[ ! -e "$APT_TEST_DIR/esm-apps" ]] || apps=enabled
  [[ ! -e "$APT_TEST_DIR/livepatch" ]] || live=enabled
  printf '{"attached":%s,"services":[{"name":"esm-infra","status":"%s"},{"name":"esm-apps","status":"%s"},{"name":"livepatch","status":"%s"}]}' "$attached" "$infra" "$apps" "$live"
}
'''
    for name, args, mode, fail_apt, success, second in (
        ('new-esm', '', '', '', True, True),
        ('infra-only', '', 'infra-only', '', True, True),
        ('apps-only', '', 'apps-only', '', True, True),
        ('existing-esm', '', 'existing', '', True, False),
        ('livepatch-only', '', 'livepatch-only', '', True, False),
        ('unattached', '', 'unattached', '', True, False),
        ('enable-failure', '', 'enable-failure', '', False, False),
        ('partial-failure', '', 'partial-failure', '', False, True),
        ('unconfirmed', '', 'unconfirmed', '', False, False),
        ('verify-failure', '', 'verify-failure', '', False, False),
        ('no-upgrade', '--no-upgrade', '', '', True, False),
        ('nopro', '--nopro', '', '', True, False),
        ('autoremove', '--autoremove', '', '', True, True),
        ('no-upgrade-autoremove', '--no-upgrade --autoremove', '', '', True, False),
        ('nopro-autoremove', '--nopro --autoremove', '', '', True, False),
        ('all-skips', '--nopro --no-upgrade --autoremove', '', '', True, False),
        ('initial-failure', '', '', 'dist-upgrade', False, False),
        ('esm-upgrade-failure', '', '', 'upgrade', False, True),
        ('index-failure', '', '', 'update', False, False),
        ('base-failure', '', '', 'install', False, False),
        ('cleanup-failure', '', '', 'autoclean', False, True),
    ):
        path = pathlib.Path(directory) / name
        path.mkdir()
        run(prefix + isolated + mocks + f'\nmain {args}', success=success,
            env={'APT_TEST_DIR': str(path), 'PRO_MODE': mode, 'FAIL_APT': fail_apt,
                 'UBUNTU_PRO_TOKEN': ''})
        calls = (path / 'calls').read_text().splitlines()
        apt = [c for c in calls if c.startswith('apt ')]
        expected = ([] if '--no-upgrade' in args else ['update', 'dist-upgrade'])
        expected += ['update', 'install', 'update']
        if second:
            expected += ['upgrade']
        if '--autoremove' in args:
            expected += ['autoremove']
        expected += ['autoclean']
        if fail_apt:
            expected = expected[:expected.index(fail_apt) + 1]
        kinds = ('update', 'dist-upgrade', 'install', 'upgrade', 'autoremove', 'autoclean')
        assert [next(k for k in kinds if k in c.split()) for c in apt] == expected

        full = [c for c in apt if ' dist-upgrade' in c]
        extra = [c for c in apt if ' upgrade ' in c]
        assert len(full) == int('--no-upgrade' not in args and fail_apt != 'update')
        assert len(extra) == int(second)
        for call in full + extra:
            for option in ('--no-remove', '-y', 'DPkg::Lock::Timeout=600',
                           'Dpkg::Options::=--force-confdef', 'Dpkg::Options::=--force-confold'):
                assert option in call
        if extra:
            assert '--with-new-pkgs' in extra[0]
        if success or mode in ('enable-failure', 'partial-failure', 'unconfirmed', 'verify-failure'):
            install = next(c for c in apt if ' install ' in c)
            assert 'ubuntu-pro-client' in install
            assert calls.index('key-check') < calls.index('swap') < calls.index(apt[0])
            if full:
                assert calls.index(full[0]) < calls.index(install)
            assert calls.index(install) < calls.index('network') < calls.index('timezone')
            if '--nopro' in args:
                assert not any(c.startswith('pro ') for c in calls)
            else:
                pro_calls = [i for i, c in enumerate(calls) if c.startswith('pro ')]
                assert calls.index('timezone') < min(pro_calls)
                post_update = next(i for i in range(max(pro_calls) + 1, len(calls))
                                   if calls[i].startswith('apt ') and calls[i].endswith(' update'))
                if extra:
                    assert post_update < calls.index(extra[0])
            removals = [c for c in apt if ' autoremove ' in c]
            assert len(removals) == int('--autoremove' in args)
            clean = next(c for c in apt if c.endswith(' autoclean'))
            assert apt[-1] == clean
            if removals:
                assert calls.index(install) < calls.index(removals[0]) < calls.index(clean)
                assert any(c.startswith('mark manual ') for c in calls[:calls.index(removals[0])])
                if extra:
                    assert calls.index(extra[0]) < calls.index(removals[0])
            assert calls[-8:] == ['systemd', 'ssh', 'dns', 'sysctl', 'unattended', 'ufw', 'fail2ban', 'report']
        else:
            assert 'report' not in calls and 'ssh' not in calls
            assert apt[-1].split().count(fail_apt) == 1
        print(f'PASS: APT/Pro MOCK {name}; order, upgrade/removal gates and errors')

# Real hidden TTY input feeding the real Pro function, with host/APT mocks above.
with tempfile.TemporaryDirectory() as directory:
    path = pathlib.Path(directory)
    calls = path / 'calls'
    lock = path / 'lock'
    isolated = main.replace('/run/lock/vps-bootstrap-proms.lock', str(lock))
    isolated = isolated.replace('/run/vps-bootstrap-proms.XXXXXX', directory + '/state.XXXXXX')
    token_mocks = r'''
# Every mocked stage checks a real child's environment, without printing secrets.
record() {
  python3 -c 'import os; assert "UBUNTU_PRO_TOKEN" not in os.environ'
  printf '%s\n' "$*" >> "$APT_TEST_DIR/calls"
}
eval "$(declare -f pro | sed '1s/^pro /original_pro /')"
pro() {
  if [[ $1 == attach ]]; then
    [[ "$*" == "attach --no-auto-enable --attach-config $STATE_DIR/pro-attach.yaml" ]] || exit 99
    python3 - "$STATE_DIR/pro-attach.yaml" <<'CHECK'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
assert json.loads(p.read_text()) == {"token": "CI_SECRET_CANARY"}
assert p.stat().st_mode & 0o077 == 0
CHECK
    record attach
    PRO_MODE=existing
  else
    original_pro "$@"
  fi
}
final_report() {
  [[ -z ${UBUNTU_PRO_TOKEN+x} && ! -e "$STATE_DIR/pro-attach.yaml" ]] || exit 99
  record report
}
'''
    for name, args, answer, inherited, mode, prompts, attaches in (
        ('typed', '--reboot', b'CI_SECRET_CANARY\n', '', 'unattached', 1, 1),
        ('empty', '--reboot', b'\n', '', 'unattached', 1, 0),
        ('eof', '--reboot', b'\x04', '', 'unattached', 1, 0),
        ('partial-eof', '--reboot', b'partial\x04\x04', '', 'unattached', 1, 0),
        ('environment', '--reboot', b'wrong\n', 'CI_SECRET_CANARY', 'unattached', 0, 1),
        ('attached', '--reboot', b'CI_SECRET_CANARY\n', '', 'existing', 1, 0),
        ('nopro', '--nopro', b'', 'CI_SECRET_CANARY', 'unattached', 0, 0),
        ('nopro-reboot', '--nopro --reboot', b'', '', 'unattached', 0, 0),
        ('help', '--help', b'', '', 'unattached', 0, 0),
        ('short-help', '-h', b'', '', 'unattached', 0, 0),
        ('noninteractive', '--reboot </dev/null', b'', '', 'unattached', 0, 0),
    ):
        calls.write_text('')
        lock.unlink(missing_ok=True)
        code = (prefix + isolated + mocks + token_mocks +
                f'\nexport APT_TEST_DIR={shlex.quote(directory)}; '
                f'PRO_MODE={mode}; main {args}')
        # Set inherited token before sourcing, just as with a process environment.
        code = f'export UBUNTU_PRO_TOKEN={shlex.quote(inherited)}; ' + code
        output = terminal(code, token_answer=answer)
        trace = calls.read_text().splitlines()
        assert 'CI_SECRET_CANARY' not in output + calls.read_text()
        assert output.count('Ubuntu Pro token (Enter to skip):') == prompts
        assert trace.count('attach') == attaches
        if attaches:
            assert trace.index('timezone') < trace.index('attach')
        if prompts:
            assert trace[:3] == ['root', 'inputs', 'os']
        print(f'PASS: early Pro token MOCK {name}')
