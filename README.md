# Ubuntu VPS Tools

Personal Ubuntu VPS bootstrap and infrastructure tools.

The bootstrap creates a 1 GiB `/swapfile` on ext4/XFS when no swap is active
and at least 4 GiB of disk space is free. Existing swap is preserved.
Use `--noswap` to skip only automatic creation. Unsupported filesystems,
insufficient space and existing inactive `/swapfile` configurations are skipped
with a warning. Creation errors are non-fatal; no swappiness changes are made.

## Bootstrap usage

```bash
sudo bash vps-bootstrap-ubuntu24.sh [options]
```

- `--ssh-port PORT`: Select Fail2ban SSH ports (comma-separated; default auto), without changing sshd listeners.
- `--no-upgrade`: Skip both package upgrades; keep base installation, APT index refreshes and Pro setup.
- `--autoremove`: Remove unused packages once after base installation and upgrades; removal is opt-in.
- `--skip-dns`: Preserve the current DNS configuration.
- `--noswap`: Skip automatic swap creation and preserve existing swap.
- `--nopro`: Skip Ubuntu Pro setup without changing the existing subscription.
- `--reboot`: Schedule a safe delayed reboot after successful completion, only if required; default is no installer reboot.
- `-h`, `--help`: Show usage and environment overrides.

Environment overrides: `TIMEZONE` (server timezone), `AUTO_REBOOT_TIME`
(unattended reboot time), `SSH_PORT` (Fail2ban ports), `IGNORE_IPS` (Fail2ban
ignore list), and `UBUNTU_PRO_TOKEN` (optional Pro attachment token).
The main no-remove dist-upgrade precedes Pro setup; newly enabled ESM repositories
receive an additional no-remove upgrade after the APT index refresh.

## Standalone root SSH keys

`update-sshid` uses the bootstrap's managed key set and file format. It replaces
all foreign/provider authorized_keys entries, backing up the previous file.
It does not install packages or replace the bootstrap's installed updater.

```bash
(
  script=$(mktemp) || exit 1
  trap 'rm -f -- "$script"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  curl -fsSL https://raw.githubusercontent.com/xPROMSx/ubuntu-vps-tools/main/update-sshid -o "$script" &&
    bash -n "$script" && sudo bash "$script"
)
```

The interactive menu offers:

1. Update managed SSH keys only; sshd configuration stays unchanged.
2. Update keys, then disable SSH password login using the bootstrap's policy.
   Conflicting policy or failed runtime application triggers SSH configuration recovery.
3. Exit without changes.

Both working options remove foreign/provider keys from `authorized_keys`.
On first use, verify a new key login in a second SSH session before closing the
current one. An interactive terminal is required; `--help` shows usage and
`--nopass` is no longer accepted. The main URL becomes available after merge;
do not run installation on a development host.
