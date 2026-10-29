#!/usr/bin/env bash
# Executed only inside a disposable QEMU guest, never on the Actions runner.
set -Eeuo pipefail
set +x
umask 077
[[ -f /opt/ci/disposable-guest ]] || exit 90
stage=${1:?stage required}
port=${2:?forwarded SSH port required}
fail() { echo "FAIL: $*"; exit 1; }
trap 'echo "FAIL: guest assertion at line $LINENO"' ERR
ssh_test() {
  local rc
  if ssh -i /root/ci-login -o IdentitiesOnly=yes -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -p "$port" \
    root@10.0.2.2 "$@" 2>/root/ci-ssh-error; then
    return 0
  else
    rc=$?
    # Safe client errors only; no key bodies or verbose private configuration.
    awk '/Permission denied|Connection refused|Connection timed out|No route|Could not resolve|Host key verification|ssh:/ {print "FAIL: SSH " $0}' /root/ci-ssh-error >&2
    return "$rc"
  fi
}

bootstrap() {
  local name=$1; shift
  # Key stays in guest; connect through host forwarding, not a local loopback exemption.
  if ! ssh_test "bash /opt/ci/bootstrap.sh $* > /root/$name.log 2>&1"; then
    fail "production bootstrap $name returned failure (log retained only in guest)"
  fi
  python3 - "/root/$name.log" <<'PY'
import re,subprocess,sys
text=re.sub(r'\x1b\[[0-9;]*m','',open(sys.argv[1]).read())

def check(condition, label):
    if condition:
        return
    print('\nFAIL: ' + label, flush=True)
    # Only known installer status phrases; never echo arbitrary log contents.
    match=re.search(r'\[(✓|!|i|✗)\] Swap\s+([^\r\n]+)', text)
    known=(
        '1 GiB active; persistent', 'existing swap preserved', 'skipped by --noswap',
        'skipped; could not inspect active swap',
        'skipped; existing inactive /swapfile requires manual review',
        'skipped; /etc/fstab is not a regular non-symlink file',
        'skipped; could not read /etc/fstab',
        'skipped; existing /swapfile fstab entry requires manual review',
        'skipped; could not determine filesystem', 'skipped; unsupported filesystem',
        'skipped; could not determine free space', 'skipped; less than 4 GiB free',
        'skipped; creation/activation failed',
        '1 GiB active; persistence failed; review /etc/fstab manually',
        '1 GiB active; persistence not confirmed; review /etc/fstab manually',
        'active; temporary file cleanup failed; review manually',
        'activation could not be confirmed; file retained for manual review',
    )
    observed=('[' + match[1] + '] ' + match[2]) if match and match[2] in known else 'missing or unrecognized (redacted)'
    print('DIAG: Observed Swap summary: ' + observed, flush=True)
    # Whitelisted errors already in the private guest log, not raw stderr/log lines.
    for error in ('Invalid argument', 'swapfile has holes', 'Permission denied',
                  'Operation not permitted', 'Device or resource busy', 'Cannot allocate memory'):
        if re.search(r'^(?:swapon|mkswap):[^\n]*' + re.escape(error), text, re.M):
            print('DIAG: swap tool error: ' + error, flush=True)
    commands=(
        ('Active swap TYPE/SIZE bytes', ['swapon','--show=TYPE,SIZE','--bytes','--noheadings'], r'(?:file|partition)\s+[0-9]+(?:\s+(?:file|partition)\s+[0-9]+)*'),
        ('/swapfile size:uid:gid:mode:nlink', ['stat','-c','%s:%u:%g:%a:%h','/swapfile'], r'[0-9:]+'),
        ('Filesystem', ['findmnt','-n','-o','FSTYPE','-T','/'], r'(?:ext4|xfs|btrfs|zfs|tmpfs|overlay|nfs|nfs4)'),
        ('Free bytes', ['df','-B1','--output=avail','/'], r'[0-9]+'),
        ('/swapfile fstab entries', ['awk','$1 == "/swapfile" {n++} END {print n+0}','/etc/fstab'], r'[0-9]+'),
    )
    for name, command, pattern in commands:
        result=subprocess.run(command, capture_output=True, text=True)
        value=result.stdout.strip()
        if name == 'Free bytes':
            value=value.splitlines()[-1] if value else ''
        safe=value if result.returncode == 0 and re.fullmatch(pattern, value) else 'none/unavailable'
        print('DIAG: ' + name + ': ' + safe, flush=True)
    raise SystemExit(1)

check('RESULT: SUCCESS WITH WARNINGS' in text or 'RESULT: SUCCESS\n' in text,
      'bootstrap success RESULT assertion')
check('FAILED CHECK:' not in text, 'bootstrap absence of FAILED CHECK assertion')
if sys.argv[1].endswith('/initial.log'):
    check('Full upgrade and autoclean completed' in text, 'default package upgrade completion assertion')
    check(re.search(r'\[✓\] Swap\s+1 GiB active; persistent', text),
          'swap initial provisioning assertion; expected [✓] 1 GiB active; persistent')
else:
    check(re.search(r'\[i\] Swap\s+existing swap preserved', text),
          'swap rerun preservation assertion; expected [i] existing swap preserved')
allowed=(
 'No Ubuntu Pro token provided;', 'Automatic package removal skipped;',
 'A local key check cannot prove remote login.', 'Global DoT does not override',
 'Additional effective root SSH authority detected: authorizedkeyscommand /opt/ci/key-helper',
 'Package upgrades were skipped by --no-upgrade',
 'Cannot inspect IPv6 configuration;', 'Provider IPv6 is configured but external IPv6',
 'Current/candidate SSH connection uses IPv6', 'Current SSH address family is unknown;',
 'Temporary IPv6 guard did not provide', 'BBR is not available in this kernel;',
 'apt-daily.timer is waiting for its running APT service;',
 'apt-daily-upgrade.timer is waiting for its running APT service;',
)
warnings=re.findall(r'^WARN: (.*)$',text,re.M)
check(all(w.startswith(allowed) for w in warnings), 'bootstrap warning allowlist assertion')
print('PASS: bootstrap result; warnings explicitly classified:',len(warnings))
PY
}
verify() {
  local effective
  # A real SSH connection activates ssh.service after reboot before sshd -T.
  # shellcheck disable=SC2016 # Evaluated remotely.
  ssh_test 'test "$(id -u)" = 0' >/dev/null
  effective=$(sshd -T -C user=root,host=localhost,addr=127.0.0.1)
  for setting in 'pubkeyauthentication yes' 'passwordauthentication no' \
    'kbdinteractiveauthentication no' \
    'authorizedkeysfile .ssh/authorized_keys'; do
    grep -Fxq "$setting" <<< "$effective"
  done
  # OpenSSH releases serialize these equivalent policy values differently.
  grep -Eq '^permitrootlogin (prohibit-password|without-password)$' <<< "$effective"
  [[ $(stat -c '%u:%g:%a' /root/.ssh/authorized_keys) == 0:0:600 ]]
  grep -q '^# BEGIN PERSONAL ED25519 ' /root/.ssh/authorized_keys
  [[ $(grep -c '^sk-ssh-ed25519@openssh.com ' /root/.ssh/authorized_keys) -ge 2 ]]
  # The guest-only test identity must not be in the managed authorized_keys.
  if grep -Fq "$(cut -d ' ' -f2 /root/ci-login.pub)" /root/.ssh/authorized_keys; then
    fail 'foreign CI identity in managed authorized_keys'
  fi
  ssh-keygen -lf /root/.ssh/authorized_keys >/dev/null
  # Negative login uses loopback (existing Fail2ban ignoreip), avoiding a test-induced
  # ban of the forwarded control connection after repeated assertions.
  if ssh -o BatchMode=yes -o PubkeyAuthentication=no \
    -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p 22 root@127.0.0.1 true 2>/dev/null; then
    fail 'non-key root login succeeded'
  fi
  ss -ltn | grep ':22 ' >/dev/null
  grep -Fxq DNSOverTLS=yes /etc/systemd/resolved.conf.d/90-proms-dot.conf
  systemctl is-active --quiet systemd-resolved
  resolvectl flush-caches
  resolvectl query --cache=no ubuntu.com >/dev/null
  resolvectl query --cache=no cloudflare.com >/dev/null
  [[ $(sysctl -n net.ipv4.tcp_fastopen) == 3 ]]
  [[ $(sysctl -n net.core.rmem_max) == 16777216 ]]
  [[ $(sysctl -n net.core.wmem_max) == 16777216 ]]
  if sysctl net.ipv4.tcp_available_congestion_control | grep -w bbr >/dev/null; then
    [[ $(sysctl -n net.ipv4.tcp_congestion_control) == bbr ]]
    [[ $(sysctl -n net.core.default_qdisc) == fq ]]
  fi
  if ip -6 route show default | grep '^unreachable default.*metric 1' >/dev/null; then
    fail 'bootstrap IPv6 guard retained'
  fi
  ufw status | grep -Fx 'Status: active' >/dev/null
  grep -Fxq 'DEFAULT_INPUT_POLICY="DROP"' /etc/default/ufw
  grep -Fxq 'DEFAULT_OUTPUT_POLICY="ACCEPT"' /etc/default/ufw
  grep -Fxq IPV6=yes /etc/default/ufw
  ufw status | grep -E '22/tcp.*ALLOW' >/dev/null
  ufw status | grep -E '22/tcp \(v6\).*ALLOW' >/dev/null
  systemctl is-enabled --quiet ufw
  systemctl is-active --quiet fail2ban
  fail2ban-client status sshd >/dev/null
  if fail2ban-client status recidive >/dev/null 2>&1; then fail 'recidive active'; fi
  for unit in unattended-upgrades apt-daily.timer apt-daily-upgrade.timer; do
    systemctl is-active --quiet "$unit"
    systemctl is-enabled --quiet "$unit"
  done
  apt-config dump | grep -F 'Unattended-Upgrade::Automatic-Reboot "true"' >/dev/null
  apt-config dump | grep -F 'Unattended-Upgrade::Automatic-Reboot-Time "04:38"' >/dev/null
  [[ $(timedatectl show -p Timezone --value) == Europe/Moscow ]]
  pro status --format json | python3 -c 'import json,sys; assert json.load(sys.stdin)["attached"] is False'
  [[ -z $(dpkg --audit) ]]
  [[ $(stat -c '%s:%u:%g:%a' /swapfile) == 1073741824:0:0:600 ]]
  swapon --show=NAME --noheadings | grep -Fx /swapfile >/dev/null
  [[ $(awk '$1 == "/swapfile" && $3 == "swap" {n++} END {print n+0}' /etc/fstab) == 1 ]]
  [[ $(stat -c %i /swapfile) == "$(cat /root/ci-swap-inode)" ]]
  echo 'PASS: real 1 GiB swap active; root:root 0600; persistent and same inode'
  echo 'PASS: real SSH/DoT/sysctl/UFW/Fail2ban/timers/Pro/dpkg state'
}
snapshot() {
  sha256sum /etc/fstab /root/.ssh/authorized_keys /etc/ssh/sshd_config /etc/default/ufw /etc/ufw/user.rules \
    /etc/ufw/user6.rules /etc/ssh/sshd_config.d/00-proms-hardening.conf \
    /etc/systemd/resolved.conf.d/90-proms-dot.conf /etc/sysctl.d/99-proms-network.conf \
    /etc/fail2ban/fail2ban.local /etc/fail2ban/jail.d/sshd.local \
    /etc/apt/apt.conf.d/20auto-upgrades /etc/apt/apt.conf.d/90-proms-unattended-upgrades \
    /etc/systemd/system/apt-daily.timer.d/90-proms-schedule.conf \
    /etc/systemd/system/apt-daily-upgrade.timer.d/90-proms-schedule.conf
}
case "$stage" in
  install)
    cloud-init status --wait >/dev/null
    ssh-keygen -q -t ed25519 -N '' -f /root/ci-login
    printf '#!/bin/sh\ncat /root/ci-login.pub\n' > /opt/ci/key-helper
    chmod 700 /opt/ci/key-helper
    printf 'AuthorizedKeysCommand /opt/ci/key-helper\nAuthorizedKeysCommandUser root\n' \
      > /etc/ssh/sshd_config.d/99-ci-authority.conf
    # Test fixture validates config before production ensure_sshd_runtime_dir runs.
    install -d -m 0755 -o root -g root /run/sshd
    if ! sshd -t 2>/root/ci-sshd-error; then
      sed 's/^/FAIL: sshd fixture /' /root/ci-sshd-error >&2
      fail 'invalid test authority fixture'
    fi
    if systemctl is-active --quiet ssh.service; then systemctl reload ssh.service; fi
    # Readiness is already established by cloud-init; this connection is a real assertion.
    ssh_test true >/dev/null
    cat /proc/sys/kernel/random/boot_id > /root/ci-boot-id
    [[ -z $(swapon --show=NAME --noheadings) && ! -e /swapfile && ! -L /swapfile ]]
    bootstrap initial
    stat -c %i /swapfile > /root/ci-swap-inode
    verify
    snapshot > /root/ci-before
    bootstrap rerun --no-upgrade
    verify
    snapshot > /root/ci-after
    cmp /root/ci-before /root/ci-after
    # APT really upgraded; the first invocation was not --no-upgrade.
    test -s /var/log/apt/history.log
    echo 'PASS: full installation + idempotent no-upgrade rerun'
    ;;
  reboot)
    cloud-init status --wait >/dev/null
    [[ $(cat /proc/sys/kernel/random/boot_id) != "$(cat /root/ci-boot-id)" ]]
    verify
    snapshot > /root/ci-reboot
    cmp /root/ci-before /root/ci-reboot
    bootstrap post-reboot --no-upgrade
    verify
    snapshot > /root/ci-after-reboot
    cmp /root/ci-before /root/ci-after-reboot
    echo 'PASS: reboot persistence + repeated bootstrap'
    ;;
  recovery)
    # Fault simulation is explicitly separate from the real successful installation.
    (
      # shellcheck disable=SC1091 # Provisioned from the exact checked-out commit.
      source /opt/ci/bootstrap.sh
      trap - EXIT
      STATE_DIR=$(mktemp -d)
      cp -a /etc/systemd/resolved.conf.d/90-proms-dot.conf "$STATE_DIR/original"
      old_link=$(readlink /etc/resolv.conf)
      # timeout launches an external command, so a resolvectl shell function is not used.
      # shellcheck disable=SC2329,SC2317 # Called indirectly by production configure_resolved.
      timeout() {
        if [[ ${2:-} == resolvectl && ${3:-} == query ]]; then return 1; fi
        command timeout "$@"
      }
      configure_resolved > /root/ci-recovery.log 2>&1
      [[ ${#FAILED_CHECKS[@]} == 1 && -z "$TX_SERVICE" ]]
      cmp "$STATE_DIR/original" /etc/systemd/resolved.conf.d/90-proms-dot.conf
      [[ $(readlink /etc/resolv.conf) == "$old_link" ]]
      command systemctl is-active --quiet systemd-resolved
      rm -rf "$STATE_DIR"
    )
    echo 'PASS: SIMULATION forced DNS query failure; real file restore/service recovery'
    ;;
  *) fail 'unknown guest test stage' ;;
esac
