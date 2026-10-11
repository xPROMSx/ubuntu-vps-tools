"""Disposable official Cloud Image VM. No private key ever leaves the guest."""
import base64
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = pathlib.Path('/tmp/bootstrap-ci')
WORK.mkdir(exist_ok=True)
DIAG = WORK / 'diagnostics.txt'
DIAG.write_text('VM integration started\n')
release = sys.argv[1]
codename = {'24.04': 'noble', '26.04': 'resolute'}[release]


def record(text):
    print(text, flush=True)
    with DIAG.open('a') as handle:
        handle.write(text + '\n')


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def download(url, target):
    # Retry only transport failures or HTTP 502/503/504, never assertion failures.
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=120) as response, target.open('wb') as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
        time.sleep(5 * (attempt + 1))


qemu = None
console = None
try:
    url = f'https://cloud-images.ubuntu.com/{codename}/current/'
    image_name = f'{codename}-server-cloudimg-amd64.img'
    sums = WORK / 'SHA256SUMS'
    image = WORK / image_name
    download(url + 'SHA256SUMS', sums)
    download(url + image_name, image)
    matches = [line.split()[0] for line in sums.read_text().splitlines()
               if line.split()[-1].lstrip('*') == image_name]
    assert len(matches) == 1, 'Missing/ambiguous official checksum'
    digest = hashlib.file_digest(image.open('rb'), 'sha256').hexdigest()
    assert digest == matches[0], 'Official image checksum mismatch'
    record(f'Image: {url + image_name}; SHA256: {digest}')
    fmt = json.loads(command('qemu-img', 'info', '--output=json', str(image)))['format']
    disk = WORK / 'guest.qcow2'
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-F', fmt, '-b', str(image),
                    str(disk), '40G'], check=True)
    files = []
    for path, source in [('/opt/ci/bootstrap.sh', ROOT / 'vps-bootstrap-ubuntu24.sh'),
                         ('/opt/ci/guest.sh', ROOT / 'tests/ci/guest.sh')]:
        files.append({'path': path, 'permissions': '0700', 'encoding': 'b64',
                      'content': base64.b64encode(source.read_bytes()).decode()})
    files.extend([
        {'path': '/opt/ci/disposable-guest', 'content': 'ephemeral CI guest\n'},
        {'path': '/etc/systemd/system/serial-getty@ttyS0.service.d/autologin.conf',
         'content': '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin root --noclear %I $TERM\n'},
    ])
    # JSON is YAML-compatible. Test keys are generated later inside the guest.
    userdata = {'hostname': 'bootstrap-ci', 'disable_root': False, 'ssh_pwauth': False,
                'write_files': files,
                'runcmd': [['systemctl', 'daemon-reload'],
                           ['systemctl', 'restart', 'serial-getty@ttyS0.service']]}
    (WORK / 'user-data').write_text('#cloud-config\n' + json.dumps(userdata))
    (WORK / 'meta-data').write_text('instance-id: bootstrap-ci\nlocal-hostname: bootstrap-ci\n')
    subprocess.run(['cloud-localds', str(WORK / 'seed.img'), str(WORK / 'user-data'),
                    str(WORK / 'meta-data')], check=True)
    # Bind an available local port. No changes to runner firewall/network/services.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    accel = 'kvm' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'tcg'
    record(f'Ubuntu {release}: accelerator={accel}; bootstrap={command("git", "rev-parse", "HEAD")}')
    serial = WORK / 'serial.sock'
    qemu = subprocess.Popen([
        'qemu-system-x86_64', '-accel', accel, '-machine', 'q35', '-m', '4096', '-smp', '2',
        '-cpu', 'host' if accel == 'kvm' else 'max', '-display', 'none', '-monitor', 'none',
        '-serial', f'unix:{serial},server=on,wait=off',
        '-drive', f'file={disk},if=virtio,format=qcow2',
        '-drive', f'file={WORK / "seed.img"},if=virtio,format=raw,readonly=on',
        '-netdev', f'user,id=net0,hostfwd=tcp:127.0.0.1:{port}-:22',
        '-device', 'virtio-net-pci,netdev=net0',
    ], stdout=subprocess.DEVNULL, stderr=(WORK / 'qemu.log').open('w'))
    deadline = time.monotonic() + 60
    while not serial.exists():
        if qemu.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError('QEMU did not create console')
        time.sleep(1)
    console = socket.socket(socket.AF_UNIX)
    console.connect(str(serial))
    console.settimeout(1)

    def wait_for(pattern, seconds):
        deadline = time.monotonic() + seconds
        buffer = ''
        while time.monotonic() < deadline:
            if qemu.poll() is not None:
                raise RuntimeError('QEMU exited unexpectedly')
            try:
                data = console.recv(65536)
                if not data:
                    raise RuntimeError('VM console disconnected')
                buffer = (buffer + data.decode(errors='replace'))[-200000:]
                # Only CI result/assertion lines are persisted; no guest configs/key material.
                for line in data.decode(errors='replace').splitlines():
                    if re.match(r'^(PASS|FAIL): ', line):
                        record(line)
                match = re.search(pattern, buffer)
                if match:
                    return match
            except socket.timeout:
                continue
        raise TimeoutError('VM readiness/stage timeout; mandatory test not completed')

    wait_for(r'Cloud-init v.*finished', 900)
    console.sendall(b'\n')
    wait_for(r'root@bootstrap-ci:[^\r\n]*#', 120)

    def stage(name, seconds):
        console.sendall((f"bash /opt/ci/guest.sh {name} {port}; rc=$?; "
                         f"printf '\\n__CI_{name}_%s__\\n' \"$rc\"\n").encode())
        match = wait_for(r'__CI_' + name + r'_(\d+)__', seconds)
        assert match[1] == '0', f'Guest stage {name} failed with {match[1]}'
        record(f'PASS: mandatory stage {name} completed')

    stage('install', 3600)
    console.sendall(b'systemctl reboot\n')
    wait_for(r'Cloud-init v.*finished', 600)
    console.sendall(b'\n')
    wait_for(r'root@bootstrap-ci:[^\r\n]*#', 120)
    stage('reboot', 1200)
    stage('recovery', 120)
    record('PASS: ALL MANDATORY VM STAGES COMPLETED')
except Exception as error:
    record(f'FAIL: {type(error).__name__}: {error}')
    if (WORK / 'qemu.log').exists():
        record('QEMU: ' + (WORK / 'qemu.log').read_text()[-2000:])
    if console is not None and qemu is not None and qemu.poll() is None:
        # Targeted state only: never dump private configs, journal or authorized_keys.
        try:
            console.sendall(b"printf '\\nCI_DIAGNOSTICS\\n'; systemctl is-active ssh.socket ssh.service systemd-resolved ufw fail2ban apt-daily.timer apt-daily-upgrade.timer; ufw status; resolvectl dns; ss -ltn; printf '\\n__DIAG_END__\\n'\n")
            end = time.monotonic() + 10
            while time.monotonic() < end:
                try:
                    data = console.recv(65536).decode(errors='replace')
                    for line in data.splitlines():
                        if line and not any(word in line for word in ('printf', 'root@', '__DIAG_END__')):
                            record('DIAG: ' + line)
                    if data.strip().endswith('__DIAG_END__'):
                        break
                except socket.timeout:
                    pass
        except OSError:
            record('Diagnostic console unavailable')
    raise
finally:
    if console is not None:
        console.close()
    if qemu is not None:
        qemu.terminate()
        try:
            qemu.wait(timeout=15)
        except subprocess.TimeoutExpired:
            qemu.kill()
            qemu.wait()
