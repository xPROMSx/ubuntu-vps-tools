"""Guest-only incident evidence; SSH/systemd/APT are real, key download is a fixture."""
import concurrent.futures
import errno
import fcntl
import os
import pathlib
import pty
import re
import select
import signal
import subprocess
import threading
import time


BASE = pathlib.Path('/opt/ci')
CONFIG = pathlib.Path('/etc/ssh/sshd_config')
DROP = CONFIG.parent / 'sshd_config.d/00-proms-hardening.conf'
KEYS = pathlib.Path('/root/.ssh/authorized_keys')
PAYLOAD = pathlib.Path('/root/ci-sshid-payload')
MISSING = 'Configured SSH ports are not confirmed by live TCP listeners'


def run(*args, check=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, **kwargs)
    if check and result.returncode:
        # Never forward raw tool errors, configuration, endpoints or key material.
        raise RuntimeError('guest command failed: ' + args[0])
    return result


def state(unit):
    return run('systemctl', 'show', unit, '-p', 'ActiveState', '--value').stdout.strip()


def branch(output):
    if MISSING in output:
        return 'unconfirmed-listeners'
    if 'SSH_CONNECTION is not confirmed' in output:
        return 'unconfirmed-connection'
    if 'Current SSH port disagrees' in output:
        return 'connection-port-mismatch'
    return 'other' if ('ERROR' in output or 'ValueError' in output) else 'none'


def sample(label):
    """Whitelist diagnostics: actual ports/owners/states, never addresses or PIDs."""
    effective = run('sshd', '-T', check=False)
    ports = sorted(set(re.findall(r'^port (\d+)$', effective.stdout, re.M)))
    print(f'DIAG: {label}: configured-ports={",".join(ports)} '
          f'sshd-T-rc={effective.returncode} '
          f'service={state("ssh.service")} socket={state("ssh.socket")}', flush=True)
    for flags in ('-ltnp', '-tnpe'):
        result = run('ss', '-H', flags, check=False)
        entries = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 6 or not re.search(r'"(?:sshd[^" ]*|systemd)"', line):
                continue
            port = fields[3].rsplit(':', 1)[-1]
            if port.isdecimal():
                owner = 'sshd' if '"sshd' in line else 'systemd'
                entries.append(f'<address>:{int(port)},{owner}')
        print(f'DIAG: {label}: ss={flags} rc={result.returncode} '
              f'cgroup2-warning={"Failed to open cgroup2 by ID" in result.stderr} '
              f'stderr-present={bool(result.stderr)} entries={";".join(sorted(set(entries)))}',
              flush=True)


def detect(script):
    # Source exact production bytes; both entry points support source-only use.
    code = f'''source {BASE / script}
trap - EXIT
STATE_DIR=$(mktemp -d)
trap 'rm -rf -- "$STATE_DIR"' EXIT
detect_ssh_ports
'''
    return run('bash', '-c', code, check=False)


def menu(before_answer):
    """A controlling PTY drives the real menu/main; only curl is replaced."""
    code = f'''source {BASE / 'update-sshid'}
curl() {{ cat {PAYLOAD}.pub; }}
main
'''
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp('bash', ['bash', '-c', code])
    output = bytearray()
    answered = False
    deadline = time.monotonic() + 360
    status = None
    try:
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    chunk = os.read(fd, 65536)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if not answered and b'[1-3]:' in output:
                    before_answer()
                    os.write(fd, b'2\n')
                    answered = True
            waited, status = os.waitpid(pid, os.WNOHANG)
            if waited:
                # Drain the PTY before returning the final output.
                while select.select([fd], [], [], 0)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                break
            status = None
        else:
            raise RuntimeError('menu timeout')
        if status is None:
            _, status = os.waitpid(pid, 0)
        if not answered:
            raise RuntimeError('menu prompt not reached')
        return os.waitstatus_to_exitcode(status), output.decode(errors='replace')
    finally:
        os.close(fd)
        if status is None:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)


def dpkg_busy():
    # Same fcntl locks as dpkg/APT, not BSD flock. Never create a lock file.
    for name in ('lock', 'lock-frontend'):
        try:
            fd = os.open('/var/lib/dpkg/' + name, os.O_RDWR | os.O_NOFOLLOW)
        except FileNotFoundError:
            continue
        try:
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN):
                    return True
                raise
        finally:
            os.close(fd)
    return False


def main():
    if os.geteuid() != 0 or not (BASE / 'disposable-guest').is_file():
        raise RuntimeError('disposable root guest required')
    if not os.environ.get('SSH_CONNECTION'):
        raise RuntimeError('established SSH control connection required')
    original = {p: (p.read_bytes(), p.stat().st_mode & 0o777) if p.exists() else None
                for p in (CONFIG, DROP, KEYS)}
    socket_enabled = run('systemctl', 'is-enabled', 'ssh.socket', check=False).stdout.strip()
    failures = []

    def restore_files():
        for path, saved in original.items():
            if saved is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(saved[0])
                path.chmod(saved[1])

    def reset(socket):
        run('systemctl', 'stop', 'ssh.service', 'ssh.socket')
        run('systemctl', 'disable', 'ssh.socket')
        restore_files()
        # Explicit test fixture: demonstrate a failed attempt leaving disk passwords enabled.
        CONFIG.write_bytes(b'PasswordAuthentication yes\nPermitRootLogin yes\n' + original[CONFIG][0])
        DROP.unlink(missing_ok=True)
        KEYS.write_text('# CI replacement sentinel\n')
        KEYS.chmod(0o600)
        run('systemctl', 'daemon-reload')
        if socket:
            run('systemctl', 'start', 'ssh.socket')
        run('systemctl', 'start', 'ssh.service')
        if (state('ssh.socket') == 'active') != socket:
            raise RuntimeError('requested listener fixture not established')

    def outcome(label, before, rc, text):
        same_config = CONFIG.read_bytes() == before[0] and not DROP.exists()
        same_keys = KEYS.read_bytes() == before[1]
        effective = run('sshd', '-T').stdout
        password = re.search(r'^passwordauthentication (yes|no)$', effective, re.M)[1]
        traceback = 'Traceback (most recent call last)' in text
        ambiguous = 'no SSH changes made' in text
        print(f'DIAG: {label}: menu-rc={rc} branch={branch(text)} '
              f'keys-changed={not same_keys} config-unchanged={same_config} '
              f'disk-passwordauthentication={password} traceback={traceback} '
              f'ambiguous-message={ambiguous}', flush=True)
        # Regression contract for phase 2: success, or clean refusal with no edits.
        # Intentionally red on the current unconfirmed-listener failure path.
        if traceback or ambiguous or (rc == 0 and password != 'no') or (
                rc != 0 and not (same_config and same_keys)):
            failures.append(label)

    try:
        run('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(PAYLOAD))
        for socket in (True, False):
            label = 'SIMULATION-service-stop' if socket else 'SIMULATION-both-stop'
            reset(socket)
            before = (CONFIG.read_bytes(), KEYS.read_bytes())
            ready = threading.Event()

            def stopped_window():
                units = ['ssh.service'] if socket else ['ssh.service', 'ssh.socket']
                run('systemctl', 'stop', *units)
                ready.set()
                # Guest-only 20-second outage simulation, not a package-manager claim.
                threading.Event().wait(20)
                run('systemctl', 'start', *reversed(units))

            def answer_during_stop():
                if not ready.wait(30):
                    raise RuntimeError('stop window not established')
                if state('ssh.service') != 'inactive' or (state('ssh.socket') == 'active') != socket:
                    raise RuntimeError('requested stop window not observed')
                sample(label + '-before-menu')
                for script in ('update-sshid', 'bootstrap.sh'):
                    result = detect(script)
                    print(f'DIAG: {label}: detector={script} rc={result.returncode} '
                          f'branch={branch(result.stderr)}', flush=True)
                if state('ssh.service') != 'inactive':
                    raise RuntimeError('stop window ended before menu submission')

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                worker = pool.submit(stopped_window)
                rc, text = menu(answer_during_stop)
                sample(label + '-after-menu')
                worker.result(timeout=120)
            outcome(label, before, rc, text)

        reset(True)
        before = (CONFIG.read_bytes(), KEYS.read_bytes())
        # REAL APT: no stop mocks, package hooks, delayed maintainer scripts or fake locks.
        apt = subprocess.Popen(['apt-get', '-y', '--no-remove', 'install', '--reinstall',
                                'openssh-server'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
        overlap = False

        def answer_during_apt():
            nonlocal overlap
            deadline = time.monotonic() + 30
            while apt.poll() is None and time.monotonic() < deadline:
                if dpkg_busy():
                    overlap = True
                    break
                threading.Event().wait(0.05)
            sample('REAL-APT-before-menu')

        try:
            rc, text = menu(answer_during_apt)
            sample('REAL-APT-after-menu')
            apt_rc = apt.wait(timeout=300)
            print(f'DIAG: REAL-APT: apt-rc={apt_rc} lock-overlap={overlap}', flush=True)
            if apt_rc or not overlap:
                failures.append('REAL-APT-overlap-not-established')
            outcome('REAL-APT', before, rc, text)
        finally:
            if apt.poll() is None:
                apt.terminate()
                apt.wait(timeout=30)

        for socket in (True, False):
            reset(socket)
            label = 'reload-socket' if socket else 'reload-service'
            for script in ('update-sshid', 'bootstrap.sh'):
                bad = 0
                for _ in range(50):
                    # Parallel reload and detection, followed by immediate post-reload detection.
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        reloading = pool.submit(run, 'systemctl', 'reload', 'ssh.service')
                        during = detect(script)
                        reloading.result(timeout=120)
                    after = detect(script)
                    if during.returncode or after.returncode or state('ssh.service') != 'active':
                        bad += 1
                        if bad == 1:
                            sample(label + '-failure')
                            print(f'DIAG: {label}: detector={script} '
                                  f'during-branch={branch(during.stderr)} '
                                  f'after-branch={branch(after.stderr)}', flush=True)
                print(f'DIAG: {label}: detector={script} iterations=50 failed-pairs={bad}', flush=True)
                if bad:
                    failures.append(label + '-' + script)
    finally:
        restore_files()
        run('systemctl', 'stop', 'ssh.service', 'ssh.socket')
        if socket_enabled in ('enabled', 'enabled-runtime'):
            run('systemctl', 'enable', 'ssh.socket')
            run('systemctl', 'start', 'ssh.socket')
        run('systemctl', 'start', 'ssh.service')
        PAYLOAD.unlink(missing_ok=True)
        PAYLOAD.with_suffix('.pub').unlink(missing_ok=True)
    if failures:
        print('FAIL: SSH incident regression: ' + ', '.join(failures), flush=True)
        return 1
    print('PASS: SSH incident regression scenarios completed', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        # Exception text from external tools/files can contain sensitive data.
        print('FAIL: SSH incident harness: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
