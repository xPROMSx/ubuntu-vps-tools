"""Isolated updater regressions; no host SSH/systemd changes."""
import pathlib
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
    run(pre + 'harden_ssh() { exit 99; }; main')
    assert config.read_text() == '# original\n'
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
    recovery = run(mock + 'apply_ssh_runtime_config() { return 1; }; harden_ssh', False)
    assert 'recovery incomplete' in recovery.stderr
    assert config.read_text() == '# original\n'

print('PASS: key format/bootstrap compatibility, dedup, foreign removal, idempotence, failure preservation, CLI and SSH rollback mocks')
