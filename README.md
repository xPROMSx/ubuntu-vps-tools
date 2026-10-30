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

Without arguments in an interactive terminal, a startup menu offers the normal
run, `--reboot`, `--nopro`, their combination, or exit without changes. Any CLI
arguments bypass the menu; non-interactive runs without arguments start directly.

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

## OpenWrt: PassWall2 APK Manager

`openwrt/passwall2-apk-repo.sh` is a standalone tool for **OpenWrt 25.12.x,
APK 3, BusyBox ash and firewall4/nftables**. It restores PassWall2 with Xray
after ASU and updates installed PassWall2 without a system-wide upgrade.
It is independent of the Ubuntu bootstrap.

Download or update the script on the router with the same command. The temporary
file must download completely and pass ash syntax validation before replacement;
this does not execute it. The main URL works after merge.

```sh
# Optional; use your own HTTP proxy if direct HTTPS is unavailable:
# export PROXY='http://proxy.example:8080'
(
  set -eu
  script=$(mktemp /root/.passwall2-apk-repo.XXXXXX)
  trap 'rm -f "$script"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  url=https://raw.githubusercontent.com/xPROMSx/ubuntu-vps-tools/main/openwrt/passwall2-apk-repo.sh
  if ! (unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY
        wget -T 30 -O "$script" "$url"); then
    [ -n "${PROXY:-}" ] || exit 1
    (export http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
     unset all_proxy ALL_PROXY no_proxy NO_PROXY
     wget -T 30 -O "$script" "$url")
  fi
  test -s "$script"
  busybox ash -n "$script"
  chmod 0600 "$script"
  mv "$script" /root/passwall2-apk-repo.sh
)
```

Without arguments, it checks signed-feed versions, the configured Xray binary
and proposed changes, then prints all commands even when the network check
fails. Checks use temporary indexes and do not change packages, persistent
feeds, world constraints, configuration or services.

```sh
sh /root/passwall2-apk-repo.sh
sh /root/passwall2-apk-repo.sh check
sh /root/passwall2-apk-repo.sh check install
sh /root/passwall2-apk-repo.sh install
sh /root/passwall2-apk-repo.sh update
sh /root/passwall2-apk-repo.sh status
sh /root/passwall2-apk-repo.sh help
```

`status` and `help` use no network. `check`, `check install`, `install` and
`update` accept `--allow-prerelease`; known prereleases otherwise block changes
to PassWall2. An unavailable GitHub API produces an **unknown** release status.
Changes require confirmation. A suitable Xray binary is preserved even when
its APK metadata is older. No languages or additional cores are selected.
Service enable/running states are preserved; a fresh install leaves services
stopped and disabled for the owner to configure and start.
After ASU, an absent package/init script with a saved, explicitly enabled main
proxy and an existing selected node restores main-service autostart and operation.
Disabled or incomplete saved settings do not trigger this recovery; the server
is never started automatically from saved configuration alone.

Limitations: repository/key availability, explicit upstream Xray requirements,
custom binary paths and unusual dependency plans can require manual review.
SourceForge was inaccessible from development, so the retained key pin and
live feed still need owner verification before router use. No real router
installation has been tested. See [OpenWrt notes](openwrt/README.md) for
provenance, failure handling and offline test limits.
