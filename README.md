# ubuntu-vps-tools

Personal scripts for setting up my own servers. They are not meant for anyone else.

Do not run them on your server. They replace SSH keys and change SSH, firewall, DNS and system update settings, and they can lock you out of your machine or break its services.

## Memo for the owner

Run the bootstrap on the Ubuntu server:

```bash
curl -fsSL https://raw.githubusercontent.com/xPROMSx/ubuntu-vps-tools/main/vps-bootstrap-ubuntu24.sh -o /tmp/vps-bootstrap-ubuntu24.sh && bash /tmp/vps-bootstrap-ubuntu24.sh
```

Update SSH keys on the server:

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

Download or update the PassWall2 manager on the OpenWrt router:

```sh
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

Open the PassWall2 menu on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh
```

Check available PassWall2 updates on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh check
```

Update PassWall2 on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh update
```

Install or restore PassWall2 on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh install
```

Show local PassWall2 status on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh status
```

Show PassWall2 help on the OpenWrt router:

```sh
sh /root/passwall2-apk-repo.sh help
```
