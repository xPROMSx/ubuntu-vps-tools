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
When inactive UFW is activated, existing IP forwarding with restrictive routed
policy triggers a warning instead of skipping activation. Forwarded traffic
may be blocked; the script does not rewrite forwarding or routed defaults.

## Standalone root SSH keys

`update-sshid` uses the bootstrap's managed key set and file format. It replaces
all foreign/provider authorized_keys entries, backing up the previous file.
It does not install packages or replace the bootstrap's installed updater.
The standalone updater supports Ubuntu 24.04/26.04 and Debian 13 (Trixie);
the bootstrap itself remains Ubuntu-only.

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

Пункт 2 сначала проверяет, закончились ли cloud-init, пакетные операции и
переходы состояния SSH. Если система занята, он ждёт до 300 секунд, проверяя
состояние каждые 5 секунд. В это время ключи и конфигурация SSH не меняются;
Ctrl+C прерывает ожидание. Если оба SSH-юнита не активны, скрипт тоже ждёт их
возвращения в том же окне. Если они не вернулись, скрипт отказывает до изменения
ключей, сообщает о сохранении прежних настроек пароля и предлагает проверить
состояние SSH-юнитов. Пункт 1 этого ожидания не выполняет.

После ожидания пункт 2 сначала устанавливает ключи, затем отключает пароль.
Поэтому при более позднем отказе ключи уже могут быть обновлены. Итог отдельно
показывает ключи, вход по паролю и состояние конфигурации SSH. Если пароль был
разрешён и отключить его не удалось, он может остаться разрешённым; смотрите
фактическое значение в итоге. Уже запрещённый пароль не объявляется включённым
из-за ошибки. Значение берётся из свежей конфигурации на диске и не доказывает
внешний доступ. Если проверить его не удалось, выводится «неизвестно».

При ошибке проверки слушателей скрипт сохраняет диагностику в отдельный файл
с правами 0600 и показывает путь. Адреса скрыты, PID и ключи не выводятся.
Не закрывайте текущую SSH-сессию. После успешного завершения проверьте вход по
ключу во второй сессии; при отказе прочитайте итог и повторите запуск после
завершения системных работ. Если откат не удался, используйте указанные файлы
восстановления и команду проверки, а не считайте прежние настройки восстановленными.
Пункт 1 тоже показывает итог: ключи, текущее значение пароля и напоминание о
пункте 2. Цвет используется только в терминале; заданный `NO_COLOR` отключает его.

Both working options remove foreign/provider keys from `authorized_keys`.
Mode 1 does not change SSH authentication: if `authorized_keys2` or other
authorization sources are configured, they may remain usable. Mode 2 enforces
`AuthorizedKeysFile .ssh/authorized_keys` for verified root contexts, so
`authorized_keys2` is not consulted; the file itself is not deleted.
Other authorization mechanisms (such as `AuthorizedKeysCommand` or trusted
SSH certificate authorities) require a separate audit.
On first use, verify a new key login in a second SSH session before closing the
current one. An interactive terminal is required; `--help` shows usage and
`--nopass` is no longer accepted. The standalone updater can be used on the supported server OS;
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

Run `sh /root/passwall2-apk-repo.sh` over an interactive SSH session to open
its menu. Select **Установка** to install or restore missing components from
the signed APK feed, **Обновление** to update PassWall2 from the official
GitHub Release, or **Проверка обновлений** for a read-only GitHub check.
After an operation or error, press Enter to return to the menu.

```sh
sh /root/passwall2-apk-repo.sh check   # first, inspect the local and latest versions
sh /root/passwall2-apk-repo.sh update  # inspect and confirm the proposed APK plan
sh /root/passwall2-apk-repo.sh install # install or restore after ASU, with confirmation
sh /root/passwall2-apk-repo.sh status  # local status only; no network
sh /root/passwall2-apk-repo.sh help
```

The GitHub APK is checked against the release asset SHA-256 and installed with
`--allow-untrusted`; this digest is not an independent developer signature.
The feed used for installation instead uses the pinned signing key and APK
signature verification. Xray and Geoview are maintained separately through
PassWall2; the Xray binary can be newer than its APK record after App Update.
See [OpenWrt notes](openwrt/README.md) for recovery, limitations and verification.
