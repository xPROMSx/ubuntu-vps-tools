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
curl -fsSL https://raw.githubusercontent.com/xPROMSx/ubuntu-vps-tools/main/update-sshid -o /tmp/update-sshid && sudo bash /tmp/update-sshid
# Optional: apply the bootstrap's root key-only SSH policy after key validation.
curl -fsSL https://raw.githubusercontent.com/xPROMSx/ubuntu-vps-tools/main/update-sshid -o /tmp/update-sshid && sudo bash /tmp/update-sshid --nopass
```

Without `--nopass`, sshd configuration is untouched. With it, conflicting policy
or runtime application triggers SSH configuration recovery. Verify a new key
login in a second SSH session before closing the current one. These main URLs
become available after this file is merged; do not run installation on a development host.
