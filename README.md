# Ubuntu VPS Tools

Personal Ubuntu VPS bootstrap and infrastructure tools.

The bootstrap creates a 1 GiB `/swapfile` on ext4/XFS when no swap is active
and at least 4 GiB of disk space is free. Existing swap is preserved.
Use `--noswap` to skip only automatic creation. Unsupported filesystems,
insufficient space and existing inactive `/swapfile` configurations are skipped
with a warning. Creation errors are non-fatal; no swappiness changes are made.

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
