#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-only
# Derived from xPROMSx/ShadowRocket-Rules/passwall2-apk-repo.sh 4.0.3
# Source commit: e690dfcaaee2bfc80e4ba1ab4643aba49aa899aa (read-only origin).
# BusyBox ash; local is supported on the target platform.
# shellcheck disable=SC3043
set -eu
umask 077
export LC_ALL=C

MAIN=luci-app-passwall2
KEY_SHA256=52802b143489214e13b78f96599a147a638205cc22d9dd6d71229504e38ddc00
KEY_URL=https://master.dl.sourceforge.net/project/openwrt-passwall-build/apk.pub
KEY_FILE=/etc/apk/keys/openwrt-passwall-build.pem
REPO_FILE=/etc/apk/repositories.d/passwall2.list
WORK='' BACKUP='' LOCKED=0 MAINTENANCE=0 CHECK=0 SHOWN=0 COMMIT=add
MODE=update ALLOW_PRERELEASE=0 SWAP_DNS='' DNS_CHANGE=0 XRAY_CHANGE=0 ADD_FIREWALL=0
PW_INST='' PW_AVAIL='' XR_INST='' XR_AVAIL='' XR_RUNTIME='' XR_PATH=''
XR_ACTION='не удалось определить' RELEASE_STATE=unknown RESULT='Не удалось проверить'
REASON='' MIN_XRAY='' TARGET_PW=''

info() { printf '%s\n' "$*"; }
fail() { REASON=$*; printf 'Error: %s\n' "$*" >&2; exit 1; }
block() { RESULT='Обновление заблокировано'; fail "$@"; }
sha() { sha256sum "$1" | awk '{print $1}'; }
compare() {
    local result
    result=$(apk version -t "$1" "$2") || fail 'APK version comparison failed.'
    case "$result" in '<'|'='|'>') printf '%s\n' "$result";; *) fail 'Invalid APK comparison.';; esac
}
valid_version() { apk version --check "$1" >/dev/null 2>&1; }
installed_version() {
    apk --no-network list --installed --manifest 2>/dev/null | awk -v p="$1" '$1==p {print $2; exit}'
}
local_status() {
    PW_INST=$(installed_version "$MAIN")
    XR_INST=$(installed_version xray-core)
    XR_PATH=$(uci -q get 'passwall2.@global_app[0].xray_file' 2>/dev/null || true)
    XR_PATH=${XR_PATH:-/usr/bin/xray}
    XR_RUNTIME=''
    if [ -x "$XR_PATH" ]; then
        XR_RUNTIME=$(timeout 10 "$XR_PATH" version 2>/dev/null | awk '$1=="Xray" {print $2; exit}')
    fi
}
commands() {
    cat <<'EOF'

Commands:
  sh /root/passwall2-apk-repo.sh
  sh /root/passwall2-apk-repo.sh check
  sh /root/passwall2-apk-repo.sh update
  sh /root/passwall2-apk-repo.sh check install
  sh /root/passwall2-apk-repo.sh install
  sh /root/passwall2-apk-repo.sh status
  sh /root/passwall2-apk-repo.sh help
  sh /root/passwall2-apk-repo.sh check --allow-prerelease
  sh /root/passwall2-apk-repo.sh check install --allow-prerelease
  sh /root/passwall2-apk-repo.sh update --allow-prerelease
  sh /root/passwall2-apk-repo.sh install --allow-prerelease
EOF
}
summary() {
    printf '\nPassWall2 APK Manager\n\nPassWall2:\n  Installed: %s\n  Available: %s\n  Prerelease: %s\n' \
        "${PW_INST:-не установлен}" "${PW_AVAIL:-не удалось проверить}" "$RELEASE_STATE"
    printf '\nXray:\n  APK installed: %s\n  APK available: %s\n  Runtime: %s (%s)\n  Action: %s\n\nResult: %s\n' \
        "${XR_INST:-не установлен}" "${XR_AVAIL:-не удалось проверить}" \
        "${XR_RUNTIME:-отсутствует или не распознан}" "${XR_PATH:-не определён}" "$XR_ACTION" "$RESULT"
    [ -z "$REASON" ] || printf 'Reason: %s\n' "$REASON"
    commands
    SHOWN=1
}
direct() (
    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY no_proxy NO_PROXY
    "$@"
)
proxy() (
    export http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY"
    unset all_proxy ALL_PROXY no_proxy NO_PROXY
    "$@"
)
# Only read/download operations are retried; never the APK commit.
network() {
    if direct "$@"; then return 0; fi
    [ -n "${PROXY:-}" ] || return 1
    printf 'Direct HTTPS failed; retrying through PROXY.\n' >&2
    proxy "$@"
}
wget_once() {
    if wget --help 2>&1 | grep -q -- '--tries'; then
        wget -t 1 -T 30 -O "$1" "$2"
    else
        wget -T 30 -O "$1" "$2"
    fi
}
fetch() {
    # wget truncates its private temporary output on each attempt.
    network wget_once "$1" "$2" && [ -s "$1" ]
}
require_system() {
    local c
    [ "$(id -u)" = 0 ] || fail 'Run as root.'
    [ -r /etc/openwrt_release ] || fail 'Only OpenWrt 25.12.x is supported.'
    # shellcheck disable=SC1091
    . /etc/openwrt_release
    printf '%s\n' "${DISTRIB_RELEASE:-}" | grep -Eq '^25[.]12[.][0-9]+$' || fail 'Only OpenWrt 25.12.x is supported.'
    case "${DISTRIB_ARCH:-}" in ''|*[!a-zA-Z0-9_-]*) fail 'Invalid package architecture.';; esac
    for c in apk uci jsonfilter wget timeout sha256sum awk sed grep mktemp readlink cmp tar flock; do
        command -v "$c" >/dev/null 2>&1 || fail "Required command missing: $c"
    done
    apk --version | grep -Eq 'apk-tools 3[.]' || fail 'APK 3 is required.'
    [ -x /sbin/fw4 ] || fail 'firewall4/nftables is required.'
    case "${PROXY:-}" in ''|http://*) ;; *) fail 'PROXY must be an HTTP proxy URL.';; esac
    BASE_URL="https://master.dl.sourceforge.net/project/openwrt-passwall-build/releases/packages-25.12/$DISTRIB_ARCH"
}
apk_run() {
    apk --repositories-file "$WORK/repositories" --keys-dir "$WORK/keys" \
        --cache-dir "$WORK/cache" --timeout 30 "$@"
}
prepare() {
    local file name
    WORK=$(mktemp -d /tmp/passwall2-apk.XXXXXX)
    mkdir -p "$WORK/keys" "$WORK/cache" "$WORK/package" "$WORK/extracted"
    for file in /etc/apk/keys/*; do
        [ ! -f "$file" ] || cp -p "$file" "$WORK/keys/"
    done
    if [ -f "$KEY_FILE" ]; then
        [ "$(sha "$KEY_FILE")" = "$KEY_SHA256" ] || block 'Repository key fingerprint mismatch; review key rotation.'
    else
        fetch "$WORK/keys/openwrt-passwall-build.pem" "$KEY_URL" || fail 'Cannot download repository key (direct HTTPS and optional PROXY).'
    fi
    [ "$(sha "$WORK/keys/openwrt-passwall-build.pem")" = "$KEY_SHA256" ] || block 'Repository key fingerprint mismatch.'
    : > "$WORK/repositories"
    for file in /etc/apk/repositories /etc/apk/repositories.d/*.list /lib/apk/repositories.d/*.list; do
        [ -f "$file" ] || continue
        name=${file##*/}
        case "$file" in /lib/*) [ ! -e "/etc/apk/repositories.d/$name" ] || continue;; esac
        awk '!/openwrt-passwall-build/' "$file" >> "$WORK/repositories"
        printf '\n' >> "$WORK/repositories"
    done
    : > "$WORK/feed"
    for name in passwall_packages passwall_luci passwall2; do
        printf '%s/%s/packages.adb\n' "$BASE_URL" "$name" >> "$WORK/feed"
    done
    cat "$WORK/feed" >> "$WORK/repositories"
    network apk_run update > "$WORK/network.log" 2>&1 || fail 'Cannot refresh signed APK repositories; check HTTPS/PROXY.'
    apk --no-network list --installed --manifest > "$WORK/installed-before"
    cp /etc/apk/world "$WORK/world-before"
}
repository_best() {
    local v best=''
    apk_run policy "$1" > "$WORK/policy"
    awk -v base="$BASE_URL/" '
        /^  [^ ]/ && /:$/ {v=$0; sub(/^  /,"",v); sub(/:$/,"",v)}
        index($0,base) && v!="" {print v}
    ' "$WORK/policy" > "$WORK/versions"
    while IFS= read -r v; do
        valid_version "$v" || continue
        if [ -z "$best" ] || [ "$(compare "$v" "$best")" = '>' ]; then best=$v; fi
    done < "$WORK/versions"
    printf '%s\n' "$best"
}
release_info() {
    local tag metadata
    tag=$(printf '%s\n' "$PW_AVAIL" | sed 's/-r/-/')
    if fetch "$WORK/release.json" "https://api.github.com/repos/Openwrt-Passwall/openwrt-passwall2/releases/tags/$tag" 2>/dev/null; then
        metadata=$(jsonfilter -i "$WORK/release.json" -e '@.tag_name' 2>/dev/null || true)
        if [ "$metadata" = "$tag" ]; then
            RELEASE_STATE=$(jsonfilter -i "$WORK/release.json" -e '@.prerelease' 2>/dev/null || true)
            case "$RELEASE_STATE" in true|false) ;; *) RELEASE_STATE=unknown;; esac
            if [ "$(jsonfilter -i "$WORK/release.json" -e '@.draft' 2>/dev/null || true)" != false ]; then RELEASE_STATE=unknown; fi
            jsonfilter -i "$WORK/release.json" -e '@.body' > "$WORK/release-notes" 2>/dev/null || true
            if grep -q "^$MAIN=" "$WORK/targets" && grep -Eiq 'breaking change|restore.*default config|incompatible config|configuration structure|migration logic' "$WORK/release-notes"; then
                block 'Release notes require manual configuration migration review.'
            fi
        fi
    fi
}
select_targets() {
    local file needed=0 pkg old
    PW_AVAIL=$(repository_best "$MAIN")
    XR_AVAIL=$(repository_best xray-core)
    [ -n "$PW_AVAIL" ] || fail 'No PassWall2 candidate in the signed feed.'
    [ -z "$PW_INST" ] || [ "$(compare "$PW_INST" "$PW_AVAIL")" != '>' ] || block 'PassWall2 downgrade blocked; installed version is newer.'
    [ -n "$PW_INST" ] || MODE=install
    : > "$WORK/targets"
    TARGET_PW=$PW_INST
    if [ -z "$PW_INST" ] || { [ "$MODE" = update ] && [ "$PW_INST" != "$PW_AVAIL" ]; }; then
        TARGET_PW=$PW_AVAIL
        printf '%s=%s\n' "$MAIN" "$PW_AVAIL" >> "$WORK/targets"
    fi
    release_info
    if [ -s "$WORK/targets" ] && [ "$RELEASE_STATE" = true ] && [ "$ALLOW_PRERELEASE" = 0 ]; then block 'Known prerelease; use --allow-prerelease to allow it.'; fi
    # Inspect the actual signed candidate, not a release table or mutable main.
    if [ "$TARGET_PW" = "$PW_AVAIL" ]; then
        network apk_run fetch --output "$WORK/package" "$MAIN=$PW_AVAIL" > "$WORK/download.log" 2>&1 || fail 'Cannot download signed PassWall2 package for compatibility inspection.'
        file="$WORK/package/$MAIN-$PW_AVAIL.apk"
        apk_run extract --destination "$WORK/extracted" "$file" >> "$WORK/download.log" 2>&1 || block 'Cannot verify/extract signed PassWall2 package.'
        file="$WORK/extracted/usr/lib/lua/luci/passwall2/util_xray.lua"
    else
        file=/usr/lib/lua/luci/passwall2/util_xray.lua
    fi
    [ -f "$file" ] || block 'Candidate has no reviewed Xray compatibility declaration.'
    MIN_XRAY=$(sed -n 's/^[[:space:]]*local xray_min_version = "\([0-9.]*\)"[[:space:]]*$/\1/p' "$file")
    [ -n "$MIN_XRAY" ] || block 'Cannot confirm Xray minimum from the signed candidate.'
    valid_version "$MIN_XRAY" || block 'Cannot confirm Xray minimum from the signed candidate.'
    if [ -n "$XR_RUNTIME" ]; then
        valid_version "$XR_RUNTIME" || block 'Cannot compare the configured Xray runtime version.'
        [ "$(compare "$XR_RUNTIME" "$MIN_XRAY")" != '<' ] || needed=1
    else
        [ ! -x "$XR_PATH" ] || block 'Configured Xray binary exists but its version is unrecognized.'
        needed=1
    fi
    XR_ACTION="подходит (upstream minimum $MIN_XRAY); сохранить бинарник"
    if [ "$needed" = 1 ]; then
        if [ "$XR_PATH" != /usr/bin/xray ] || [ -L /usr/bin/xray ]; then
            block 'Xray needs repair at a custom path/symlink; review App Update settings manually.'
        fi
        [ -n "$XR_AVAIL" ] || block 'No signed Xray package available.'
        [ "$(compare "${XR_AVAIL%-r*}" "$MIN_XRAY")" != '<' ] || block 'Signed Xray package is below the candidate requirement.'
        [ -z "$XR_INST" ] || [ "$(compare "$XR_INST" "$XR_AVAIL")" != '>' ] || block 'Xray APK downgrade blocked.'
        # A missing binary with unchanged APK metadata needs apk fix, not add.
        if [ "$XR_INST" = "$XR_AVAIL" ]; then
            if [ "$MODE" != install ] || [ -s "$WORK/targets" ]; then
                block 'Repair the missing/old Xray binary with install first, then update PassWall2.'
            fi
            COMMIT=fix
        fi
        XRAY_CHANGE=1
        XR_ACTION="установить $XR_AVAIL (ядро отсутствует или ниже $MIN_XRAY)"
        if [ "$COMMIT" = fix ]; then printf 'xray-core\n'; else printf 'xray-core=%s\n' "$XR_AVAIL"; fi >> "$WORK/targets"
    fi
    if [ "$MODE" = install ]; then
        if [ -z "$PW_INST" ] && ! uci -q get firewall.passwall2 >/dev/null 2>&1; then ADD_FIREWALL=1; fi
        for pkg in chinadns-ng dnsmasq-full kmod-nft-socket kmod-nft-tproxy kmod-nft-nat; do
            [ -n "$(installed_version "$pkg")" ] || printf '%s\n' "$pkg" >> "$WORK/targets"
        done
        if [ -z "$(installed_version dnsmasq-full)" ]; then
            DNS_CHANGE=1
            for pkg in dnsmasq dnsmasq-dhcpv6; do
                old=$(installed_version "$pkg")
                [ -n "$old" ] || continue
                [ -z "$SWAP_DNS" ] || block 'Multiple dnsmasq variants; manual review required.'
                SWAP_DNS=$pkg
                printf '!%s\n' "$pkg" >> "$WORK/targets"
            done
        fi
    fi
    if [ "$COMMIT" = fix ] && [ "$(wc -l < "$WORK/targets")" -ne 1 ]; then
        block 'Xray binary repair must run separately from missing component installation; review the installed APK state.'
    fi
}
parse_plan() {
    awk '
    /^\(/ && !/^\([[:space:]]*[0-9]+\/[0-9]+\)/ {bad=1; exit 30}
    /^\([[:space:]]*[0-9]+\/[0-9]+\)/ {
        hdr=$0; sub(/\).*/,"",hdr); gsub(/[()[:space:]]/,"",hdr); split(hdr,h,"/")
        if (h[1] != count+1 || (total && total != h[2])) {bad=1; exit 34}
        count++; total=h[2]
        s=$0; sub(/^\([[:space:]]*[0-9]+\/[0-9]+\)[[:space:]]*/,"",s)
        if (s ~ /^Updating pinning /) {a="Pinning"; sub(/^Updating pinning /,"",s)}
        else {a=s; sub(/ .*/,"",a); sub(/^[^ ]+ +/,"",s)}
        if (a !~ /^(Installing|Upgrading|Downgrading|Replacing|Reinstalling|Removing|Purging|Pinning)$/) {bad=1; exit 31}
        p=s; sub(/ .*/,"",p); sub(/@.*/,"",p)
        if (p !~ /^[a-zA-Z0-9][a-zA-Z0-9+_.-]*$/) {bad=1; exit 32}
        v=s; sub(/^[^(]*\(/,"",v); sub(/\).*/,"",v); sub(/^.* -> /,"",v)
        if (v !~ /^[a-zA-Z0-9][a-zA-Z0-9+_.:~-]*$/) {bad=1; exit 33}
        print a "|" p "|" v
    }
    END {if (bad || count != total) exit 35}
    ' "$1"
}
check_plan() {
    local action pkg version dns=0
    parse_plan "$1" > "$2" || block 'Unrecognized APK 3 action stream.'
    grep -q '^OK:' "$1" || block 'APK simulation has no success summary.'
    grep -q '^Installing|dnsmasq-full|' "$2" && dns=1
    while IFS='|' read -r action pkg version; do
        case "$action" in
            Removing|Purging)
                [ "$MODE" = install ] && [ "$pkg" = "$SWAP_DNS" ] && [ "$dns" = 1 ] && continue
                block "Package removal blocked: $pkg";;
            Downgrading|Replacing|Pinning) block "Unsafe APK action: $action $pkg";;
        esac
        case "$pkg" in
            luci-i18n-*|sing-box*|haproxy*|shadowsocks*|simple-obfs*|v2ray-plugin*|trojan*|hysteria*|naiveproxy*|tuic*)
                block "Excluded optional component required by APK plan: $pkg";;
            busybox|apk*|libc|musl*|firewall*|nftables*|dropbear*|openssh*|hostapd*|wpad*|kernel|base-files|netifd|procd*|ubus*|libubus*|libubox*|libblobmsg-json*|uci|libuci*|odhcp*|ppp*|rpcd)
                # libuci-lua is a mandatory LuCI binding, not libuci itself.
                if [ "$pkg" != libuci-lua ] || [ "$action" != Installing ]; then block "System/network package change blocked: $pkg"; fi;;
            kmod-*)
                case "$pkg" in kmod-nft-socket|kmod-nft-tproxy|kmod-nft-nat|kmod-nf-socket|kmod-nf-tproxy|kmod-nf-nat) ;; *) block "Unreviewed kernel module: $pkg";; esac
                if [ "$MODE" != install ] || [ "$action" != Installing ]; then block "Installed kernel module change blocked: $pkg"; fi;;
            dnsmasq*)
                if [ "$MODE" != install ] || [ "$pkg" != dnsmasq-full ] || [ "$action" != Installing ]; then
                    block 'DNS changes require install and a reviewed variant replacement.'
                fi;;
            xray-core) [ "$XRAY_CHANGE" = 1 ] || block 'APK would replace a suitable Xray runtime; review package dependencies manually.';;
        esac
        grep -Fxq "$pkg" "$WORK/dependencies" || block "Package outside requested dependency closure: $pkg"
        if [ "$action" = Reinstalling ]; then
            if [ "$COMMIT" != fix ] || [ "$pkg" != xray-core ]; then block "Unnecessary reinstallation: $pkg"; fi
        fi
    done < "$2"
}
package_operation() {
    if [ "$COMMIT" = fix ]; then apk_run fix --reinstall "$@"; else apk_run add "$@"; fi
}
simulate() {
    local arg
    : > "$WORK/actions"
    [ -s "$WORK/targets" ] || return 0
    set --
    while IFS= read -r arg; do set -- "$@" "$arg"; done < "$WORK/targets"
    # No --upgrade: existing dependencies change only when constraints require it.
    apk_run query --recursive --fields name "$@" > "$WORK/dependency-query" 2>&1 || block 'Cannot resolve actual package dependencies.'
    sed -n 's/^Name: //p' "$WORK/dependency-query" > "$WORK/dependencies"
    [ -s "$WORK/dependencies" ] || block 'Unknown APK dependency query format.'
    network package_operation --simulate "$@" > "$WORK/plan" 2>&1 || block 'APK dependency simulation failed; no packages changed.'
    check_plan "$WORK/plan" "$WORK/actions"
    local pkg version
    while IFS= read -r arg; do
        case "$arg" in
            !*) continue;;
            *=*) pkg=${arg%%=*}; version=${arg#*=};;
            *) pkg=$arg; version='';;
        esac
        if [ -z "$(installed_version "$pkg")" ] || { [ -n "$version" ] && [ "$(installed_version "$pkg")" != "$version" ]; } || [ "$COMMIT" = fix ]; then
            awk -F '|' -v p="$pkg" -v v="$version" '$2==p && (v=="" || $3==v) {ok=1} END {exit !ok}' "$WORK/actions" || block "APK did not plan the requested package: $arg"
        fi
    done < "$WORK/targets"
}
space_check() {
    local arg pkg version total=0 value free
    # Sum full installed sizes rather than estimating from compressed archives.
    while IFS='|' read -r arg pkg version; do
        case "$arg" in Removing|Purging) continue;; esac
        value=$(apk_run query --from repositories --fields installed-size "$pkg=$version" | awk '/^Installed-Size: / {print $2}')
        case "$value" in ''|*[!0-9]*) block "Cannot determine installed size: $pkg";; esac
        total=$((total + value))
    done < "$WORK/actions"
    # Also leave space for configuration backups and archive staging.
    total=$((total / 1024 + 16384))
    config_paths > "$WORK/config-paths"
    while IFS= read -r arg; do
        value=$(du -sk "$arg" | awk '{print $1}')
        total=$((total + value))
    done < "$WORK/config-paths"
    free=$(df -Pk /overlay | awk 'END {print $4}')
    [ "$free" -ge "$total" ] || block "Insufficient /overlay space: need $total KiB."
    free=$(df -Pk /tmp | awk 'END {print $4}')
    [ "$free" -ge "$total" ] || block "Insufficient /tmp space: need $total KiB."
}
config_paths() {
    local path asset
    asset=$(uci -q get 'passwall2.@global_rules[0].v2ray_location_asset' 2>/dev/null || true)
    asset=${asset:-/usr/share/v2ray}
    for path in /etc/config /usr/share/passwall2/direct_ip /usr/share/passwall2/domains_excluded \
        /usr/share/v2ray/geoip.dat /usr/share/v2ray/geosite.dat "$asset/geoip.dat" "$asset/geosite.dat"; do
        [ ! -e "$path" ] || printf '%s\n' "$path"
    done | sort -u
}
check_idle() {
    local changes
    changes=$(uci -q changes 2>/dev/null || true)
    [ -z "$changes" ] || block 'Save/apply pending UCI changes first.'
    # These upstream flock files persist while idle; existence is not activity.
    local lock
    for lock in /var/lock/passwall2.lock /var/lock/passwall2_sub_rule.lock /var/lock/passwall2_server.lock; do
        [ ! -e "$lock" ] || flock -n "$lock" true || block 'PassWall2 has an active operation; retry after it completes.'
    done
}
check_paths() {
    local path
    for path in /etc/config/passwall2 /etc/config/passwall2_server /etc/apk/world "$KEY_FILE" "$REPO_FILE"; do
        [ ! -L "$path" ] || block "Cannot safely maintain a symlink: $path"
    done
    for path in /etc/apk/repositories /etc/apk/repositories.d/*.list; do
        [ -f "$path" ] || continue
        if [ -L "$path" ] && grep -q openwrt-passwall-build "$path"; then block 'A PassWall feed is in a symlinked repository file; review it manually.'; fi
    done
}
processes() {
    local entry
    for entry in /proc/[0-9]*/cmdline; do
        [ -r "$entry" ] || continue
        tr '\000' '\n' < "$entry" 2>/dev/null | grep -q "^/tmp/etc/$1/" || continue
        printf '%s\n' "${entry%/cmdline}"
    done
}
main_proxy_enabled() {
    local node
    [ -s /etc/config/passwall2 ] || return 1
    [ "$(uci -q get 'passwall2.@global[0].enabled' 2>/dev/null || true)" = 1 ] || return 1
    node=$(uci -q get 'passwall2.@global[0].node' 2>/dev/null || true)
    [ -n "$node" ] || return 1
    [ "$(uci -q get "passwall2.$node" 2>/dev/null || true)" = nodes ] || return 1
    [ -n "$(uci -q get "passwall2.$node.type" 2>/dev/null || true)" ]
}
wait_main_processes() {
    local elapsed=0
    # Upstream start may legitimately finish without residents when all modes are off.
    if ! main_proxy_enabled &&
        [ "$(uci -q get 'passwall2.@global[0].socks_enabled' 2>/dev/null || true)" != 1 ] &&
        [ "$(uci -q get 'passwall2.@global[0].acl_enable' 2>/dev/null || true)" != 1 ] &&
        [ "$(uci -q get 'passwall2.@global_haproxy[0].balancing_enable' 2>/dev/null || true)" != 1 ]; then
        info 'No enabled PassWall2 proxy mode; no resident processes are expected.'
        return 0
    fi
    while [ -z "$(processes passwall2)" ]; do
        [ "$elapsed" -lt 15 ] || return 1
        sleep 1
        elapsed=$((elapsed + 1))
    done
    return 0
}
prefetch() {
    local action pkg version arg
    set --
    while IFS='|' read -r action pkg version; do
        case "$action" in Removing|Purging) continue;; esac
        set -- "$@" "$pkg=$version"
    done < "$WORK/actions"
    network apk_run cache download "$@" > "$WORK/cache.log" 2>&1 || fail 'Package download failed; system untouched.'
    set --
    while IFS= read -r arg; do set -- "$@" "$arg"; done < "$WORK/targets"
    # Pass --no-network as a global option through the same operation wrapper.
    package_operation --no-network --simulate "$@" > "$WORK/offline-plan" 2>&1 || block 'Cached packages do not satisfy the plan.'
    check_plan "$WORK/offline-plan" "$WORK/offline-actions"
    cmp -s "$WORK/actions" "$WORK/offline-actions" || block 'Plan changed while downloading.'
    apk --no-network list --installed --manifest > "$WORK/installed-now"
    cmp -s "$WORK/installed-before" "$WORK/installed-now" || block 'APK state changed during preparation; rerun check.'
    cmp -s "$WORK/world-before" /etc/apk/world || block 'APK world changed during preparation; rerun check.'
    check_idle
}
backup() {
    local path svc
    BACKUP=/root/passwall2-apk-backup
    [ ! -e "$BACKUP" ] || block 'Move the previous /root/passwall2-apk-backup aside before another change.'
    mkdir "$BACKUP"
    config_paths > "$WORK/config-paths"
    : > "$WORK/config-hashes"
    set --
    while IFS= read -r path; do
        set -- "$@" "${path#/}"
        if [ -d "$path" ]; then
            find "$path" -type f -exec sha256sum '{}' ';' >> "$WORK/config-hashes"
        else
            sha256sum "$path" >> "$WORK/config-hashes"
        fi
    done < "$WORK/config-paths"
    tar -czf "$BACKUP/config.tgz" -C / "$@" || fail 'Cannot save configuration backup.'
    cp "$WORK/world-before" "$WORK/plan" "$BACKUP/"
    : > "$WORK/services"
    for svc in passwall2 passwall2_server dnsmasq; do
        local enabled=0 running=0
        [ ! -x "/etc/init.d/$svc" ] || ! "/etc/init.d/$svc" enabled >/dev/null 2>&1 || enabled=1
        if [ "$svc" = dnsmasq ]; then
            [ ! -x /etc/init.d/dnsmasq ] || ! /etc/init.d/dnsmasq running >/dev/null 2>&1 || running=1
        else
            [ -z "$(processes "$svc")" ] || running=1
        fi
        # ASU can retain an active UCI setup while removing the package/init script.
        # Existing init scripts always retain their observed enable/running states.
        if [ "$svc" = passwall2 ] && [ -z "$PW_INST" ] &&
            [ ! -x /etc/init.d/passwall2 ] && main_proxy_enabled; then
            enabled=1
            running=1
        fi
        printf '%s|%s|%s\n' "$svc" "$enabled" "$running" >> "$WORK/services"
    done
    cp "$WORK/services" "$BACKUP/"
    # Detect a binary change (for example App Update) between plan and commit.
    if [ -x "$XR_PATH" ]; then sha "$XR_PATH" > "$WORK/xray-hash"; fi
}
service_flags() {
    local svc enabled running
    while IFS='|' read -r svc enabled running; do
        [ "$svc" != dnsmasq ] || [ "$DNS_CHANGE" = 1 ] || continue
        [ -x "/etc/init.d/$svc" ] || continue
        if [ "$enabled" = 1 ]; then "/etc/init.d/$svc" enable; else "/etc/init.d/$svc" disable; fi
    done < "$WORK/services"
}
maintenance() {
    local svc
    for svc in passwall2 passwall2_server; do
        [ ! -L "/etc/config/$svc" ] || block "Cannot safely maintain a symlinked $svc configuration."
    done
    MAINTENANCE=1
    for svc in passwall2 passwall2_server; do
        [ ! -x "/etc/init.d/$svc" ] || timeout 60 "/etc/init.d/$svc" stop || fail "Cannot stop $svc."
        # OpenWrt postinst starts init scripts. Give them an inert configuration.
        printf "config global\n option enabled '0'\n option socks_enabled '0'\n option acl_enable '0'\nconfig global_delay\n option start_daemon '0'\n" > "/etc/config/$svc"
    done
}
restore_configs() {
    # Extract only files present before the operation; new vendor files survive.
    tar -xzf "$BACKUP/config.tgz" -C / || return 1
    local svc
    for svc in passwall2 passwall2_server; do
        if ! tar -tzf "$BACKUP/config.tgz" | grep -Fxq "etc/config/$svc"; then
            # A clean install receives vendor defaults, not the inert placeholder.
            if [ -f "/usr/share/$svc/0_default_config" ]; then
                cp "/usr/share/$svc/0_default_config" "/etc/config/$svc" || return 1
            elif [ -f "$WORK/extracted/etc/config/$svc" ]; then
                cp "$WORK/extracted/etc/config/$svc" "/etc/config/$svc" || return 1
            else
                rm -f "/etc/config/$svc"
            fi
        fi
    done
    service_flags
}
persist_repo() {
    local file name tmp
    mkdir -p /etc/apk/keys /etc/apk/repositories.d
    for file in /etc/apk/repositories /etc/apk/repositories.d/*.list /lib/apk/repositories.d/*.list; do
        [ -f "$file" ] || continue
        name=${file##*/}
        case "$file" in /lib/*) [ ! -e "/etc/apk/repositories.d/$name" ] || continue;; esac
        grep -q openwrt-passwall-build "$file" || continue
        case "$file" in /lib/*) file="/etc/apk/repositories.d/$name"; cp "/lib/apk/repositories.d/$name" "$file";; esac
        [ ! -L "$file" ] || fail 'Refusing to replace a repository symlink.'
        tmp=$(mktemp "${file}.XXXXXX")
        awk '!/openwrt-passwall-build/' "$file" > "$tmp"
        chmod 0644 "$tmp"; mv "$tmp" "$file"
    done
    [ ! -L "$REPO_FILE" ] || fail 'Refusing to replace a repository symlink.'
    tmp=$(mktemp "${REPO_FILE}.XXXXXX")
    [ ! -f "$REPO_FILE" ] || cat "$REPO_FILE" > "$tmp"
    cat "$WORK/feed" >> "$tmp"
    chmod 0644 "$tmp"; mv "$tmp" "$REPO_FILE"
    cp "$WORK/keys/openwrt-passwall-build.pem" "$KEY_FILE"
    chmod 0644 "$KEY_FILE"
}
unpin_targets() {
    # Remove only constraints introduced/replaced by this transaction.
    # Keep every unrelated line verbatim; never run a second APK commit.
    local tmp
    [ ! -L /etc/apk/world ] || fail 'Refusing to edit a world symlink.'
    tmp=$(mktemp /etc/apk/world.XXXXXX)
    awk 'NR==FNR {s=$0; sub(/[=<>~].*/,"",s); sub(/^!/,"",s); target[s]=1; next}
        {s=$0; sub(/[@=<>~].*/,"",s); sub(/^!/,"",s)}
        s in target {if ($0 !~ /^!/) print s; next} {print}' "$WORK/targets" /etc/apk/world > "$tmp"
    chmod 0644 "$tmp"; mv "$tmp" /etc/apk/world
}
apply() {
    local arg svc enabled running actual
    set --
    while IFS= read -r arg; do set -- "$@" "$arg"; done < "$WORK/targets"
    # Exactly one commit, with cached signed packages. No network retry/rollback.
    package_operation --no-network "$@" > "$BACKUP/install.log" 2>&1 || fail 'APK failed; not retried. Review the backup and installed state.'
    for svc in passwall2 passwall2_server; do
        [ ! -x "/etc/init.d/$svc" ] || timeout 60 "/etc/init.d/$svc" stop || fail "Cannot stop postinst $svc."
    done
    restore_configs || fail 'Configuration restoration failed; retain the backup.'
    sha256sum -c "$WORK/config-hashes" >/dev/null 2>&1 || fail 'Configuration preservation verification failed; retain the backup.'
    if [ "$ADD_FIREWALL" = 1 ]; then
        # Required upstream integration, only an absent, application-owned section.
        uci -q set firewall.passwall2=include
        uci -q set firewall.passwall2.type=script
        uci -q set firewall.passwall2.path=/var/etc/passwall2.include
        uci -q commit firewall
    fi
    MAINTENANCE=0
    [ "$COMMIT" = fix ] || unpin_targets
    persist_repo
    local action pkg version
    while IFS='|' read -r action pkg version; do
        case "$action" in Removing|Purging) [ -z "$(installed_version "$pkg")" ] || fail "Removed package still installed: $pkg";;
            *) [ "$(installed_version "$pkg")" = "$version" ] || fail "Installed version differs from plan: $pkg";;
        esac
    done < "$WORK/actions"
    [ "$(installed_version "$MAIN")" = "$TARGET_PW" ] || fail 'Installed PassWall2 does not match the plan.'
    actual=$(timeout 10 "$XR_PATH" version 2>/dev/null | awk '$1=="Xray" {print $2; exit}')
    valid_version "$actual" || fail 'Xray runtime verification failed.'
    [ "$(compare "$actual" "$MIN_XRAY")" != '<' ] || fail 'Xray runtime verification failed.'
    if [ "$XRAY_CHANGE" = 0 ]; then
        [ "$actual" = "$XR_RUNTIME" ] || fail 'A suitable Xray runtime changed unexpectedly.'
        [ "$(sha "$XR_PATH")" = "$(cat "$WORK/xray-hash")" ] || fail 'A suitable Xray binary changed unexpectedly.'
    else
        [ "$(installed_version xray-core)" = "$XR_AVAIL" ] || fail 'Xray APK verification failed.'
    fi
    while IFS='|' read -r svc enabled running; do
        [ "$svc" != dnsmasq ] || [ "$DNS_CHANGE" = 1 ] || continue
        [ -x "/etc/init.d/$svc" ] || continue
        if [ "$running" = 1 ]; then
            timeout 180 "/etc/init.d/$svc" restart > "$BACKUP/start-$svc.log" 2>&1 || fail "Startup of $svc not confirmed; inspect the new start log."
            if [ "$svc" = dnsmasq ]; then
                /etc/init.d/dnsmasq running >/dev/null 2>&1 || fail 'dnsmasq is not running.'
            elif [ "$svc" = passwall2 ]; then
                wait_main_processes || fail 'passwall2 has no runtime processes after startup (15 second timeout).'
            else
                [ -n "$(processes "$svc")" ] || fail "$svc has no runtime processes after startup."
            fi
        else
            timeout 60 "/etc/init.d/$svc" stop || fail "Cannot preserve stopped state: $svc"
        fi
    done < "$WORK/services"
    info 'Completed. Configuration and service enable/running states preserved.'
    info 'Verify DNS and selected proxy nodes from a LAN client; local checks do not prove connectivity.'
    rm -rf "$BACKUP"
    BACKUP=''
}
cleanup() {
    local rc=$?
    trap - EXIT HUP INT TERM
    if [ "$MAINTENANCE" = 1 ]; then
        restore_configs || printf 'Configuration restoration NOT confirmed; inspect %s.\n' "$BACKUP" >&2
        printf 'Service recovery is NOT confirmed; inspect current connectivity before retrying.\n' >&2
    fi
    if [ -n "$BACKUP" ]; then
        printf 'Backup/diagnostics retained: %s; temporary evidence: %s\n' "$BACKUP" "$WORK" >&2
    elif [ -n "$WORK" ]; then rm -rf "$WORK"; fi
    [ "$LOCKED" = 0 ] || rmdir /tmp/passwall2-apk-manager.lock 2>/dev/null || true
    if [ "$CHECK" = 1 ] && [ "$SHOWN" = 0 ]; then summary; fi
    exit "$rc"
}
main() {
    local command="${1:-check}" answer
    [ "$#" = 0 ] || shift
    case "$command" in
        help|-h|--help) info 'OpenWrt 25.12.x / APK 3 / firewall4. Check is read-only; install restores missing components; update targets installed PassWall2. Known prereleases require explicit opt-in; unknown release status is reported.'; commands; return;;
        status) require_system; local_status; RESULT='Локальный статус (без сети)'; summary; return;;
        check) CHECK=1; if [ "${1:-}" = install ]; then MODE=install; shift; fi;;
        install|update) MODE=$command;;
        *) fail 'Unknown command; use help.';;
    esac
    if [ "${1:-}" = --allow-prerelease ]; then ALLOW_PRERELEASE=1; shift; fi
    [ "$#" = 0 ] || fail 'Unexpected arguments; use help.'
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    require_system
    local_status
    if [ "$command" = update ] && [ -z "$PW_INST" ]; then block 'PassWall2 is absent; use check install / install.'; fi
    mkdir /tmp/passwall2-apk-manager.lock 2>/dev/null || fail 'Another manager is running, or a stale manager lock needs review.'
    LOCKED=1
    prepare
    select_targets
    simulate
    if [ ! -s "$WORK/actions" ]; then RESULT='Уже актуально'; summary; return; fi
    check_paths
    space_check
    if [ -z "$PW_INST" ] || [ "$MODE" = install ]; then RESULT='Требуется установка'; else RESULT='Обновление доступно'; fi
    summary
    printf '\nAPK plan:\n'; cat "$WORK/plan"
    [ "$ADD_FIREWALL" = 0 ] || info 'Configuration plan: add the absent firewall.passwall2 include; preserve other firewall/DNS settings.'
    [ "$CHECK" = 0 ] || return 0
    printf '\nApply this %s plan? [y/N]: ' "$MODE"
    read -r answer || return 0
    case "$answer" in y|Y|yes|YES) ;; *) info 'Cancelled.'; return 0;; esac
    prefetch
    backup
    maintenance
    apply
}

# Source-only entry is used solely by the isolated offline tests.
if [ "${PW2_SOURCE_ONLY:-0}" != 1 ]; then main "$@"; fi
