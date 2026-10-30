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
WORK='' BACKUP='' LOCKED=0 MAINTENANCE=0 COMMIT=add
MODE=update SWAP_DNS='' DNS_CHANGE=0 XRAY_CHANGE=0 ADD_FIREWALL=0 LOCAL_APK=''
PW_INST='' PW_AVAIL='' XR_INST='' XR_AVAIL='' XR_RUNTIME='' XR_PATH=''
MIN_XRAY='' TARGET_PW=''
GREEN='' YELLOW='' RED='' RESET=''
if [ -t 1 ] && [ -z "${NO_COLOR+x}" ]; then
    case "${TERM:-}" in xterm*|screen*|tmux*|linux|rxvt*|vt100|ansi)
        GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
        RED=$(printf '\033[31m'); RESET=$(printf '\033[0m');;
    esac
fi

info() { printf '%s\n' "$*"; }
fail() { printf '%sОшибка: %s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }
block() { fail "$@"; }
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
    network wget_once "$1" "$2" >/dev/null 2>&1 && [ -s "$1" ]
}
require_system() {
    local c
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
    network apk_run update >/dev/null 2>&1 || fail 'Cannot refresh signed APK repositories; check HTTPS/PROXY.'
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
signed_package_url() {
    apk_run fetch --simulate --url --output "$WORK/package" "$MAIN" > "$WORK/package-url" 2>/dev/null
}
select_targets() {
    local file needed=0 pkg old url name version
    PW_AVAIL=$(repository_best "$MAIN")
    XR_AVAIL=$(repository_best xray-core)
    [ -n "$PW_AVAIL" ] || fail 'No PassWall2 candidate in the signed feed.'
    [ -n "$PW_INST" ] || MODE=install
    : > "$WORK/targets"
    TARGET_PW=$PW_INST
    if [ -z "$PW_INST" ] || { [ "$MODE" = update ] && [ "$PW_INST" != "$PW_AVAIL" ]; }; then
        TARGET_PW=$PW_AVAIL
        printf '%s=%s\n' "$MAIN" "$PW_AVAIL" >> "$WORK/targets"
    fi
    # Inspect the actual signed candidate, not a release table or mutable main.
    if [ "$TARGET_PW" = "$PW_AVAIL" ]; then
        network signed_package_url >/dev/null 2>&1 || fail 'Cannot resolve signed PassWall2 package URL.'
        url=$(cat "$WORK/package-url")
        case "$url" in
            "$BASE_URL/passwall_packages/$MAIN-$PW_AVAIL.apk"|"$BASE_URL/passwall_luci/$MAIN-$PW_AVAIL.apk"|"$BASE_URL/passwall2/$MAIN-$PW_AVAIL.apk") ;;
            *) block 'Selected APK URL differs from the reviewed signed-feed version/source.';;
        esac
        file="$WORK/package/$MAIN-$PW_AVAIL.apk"
        network apk_run fetch --output "$WORK/package" "$MAIN" >/dev/null 2>&1 || fail 'Cannot fetch PassWall2 package verified against the signed index.'
        apk_run adbdump "$file" > "$WORK/package-metadata" 2>/dev/null || block 'Cannot inspect signed APK metadata.'
        name=$(awk '/^info:$/ {info=1; next} info && /^  name: / {print $2; exit}' "$WORK/package-metadata")
        version=$(awk '/^info:$/ {info=1; next} info && /^  version: / {print $2; exit}' "$WORK/package-metadata")
        if [ "$name" != "$MAIN" ] || [ "$version" != "$PW_AVAIL" ]; then
            block 'Signed APK identity/version differs from the reviewed candidate.'
        fi
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
        if [ "$COMMIT" = fix ]; then printf 'xray-core\n'; else printf 'xray-core=%s\n' "$XR_AVAIL"; fi >> "$WORK/targets"
    fi
    if [ "$MODE" = install ]; then
        if [ -z "$PW_INST" ] && ! uci -q get firewall.passwall2 >/dev/null 2>&1; then ADD_FIREWALL=1; fi
        for pkg in geoview kmod-nft-socket kmod-nft-tproxy; do
            [ -n "$(installed_version "$pkg")" ] || printf '%s\n' "$pkg" >> "$WORK/targets"
        done
        # Upstream's firewall4 mode requires dnsmasq compiled with nftset.
        if ! dnsmasq --version 2>/dev/null | grep -qw nftset; then
            [ -z "$(installed_version dnsmasq-full)" ] || block 'dnsmasq-full does not provide nftset; inspect DNS manually.'
            for pkg in dnsmasq dnsmasq-dhcpv6; do
                old=$(installed_version "$pkg")
                [ -n "$old" ] || continue
                [ -z "$SWAP_DNS" ] || block 'Multiple dnsmasq variants; manual review required.'
                SWAP_DNS=$pkg
            done
            [ -n "$SWAP_DNS" ] || block 'No running dnsmasq variant found; inspect DNS manually.'
            if [ ! -x /etc/init.d/dnsmasq ] || ! /etc/init.d/dnsmasq running >/dev/null 2>&1; then
                block 'dnsmasq is not running; inspect DNS before replacement.'
            fi
            DNS_CHANGE=1
            printf '%s\n' 'dnsmasq-full' "!$SWAP_DNS" >> "$WORK/targets"
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
    case "$COMMIT" in
        fix) apk_run fix --reinstall "$@";;
        local) apk --allow-untrusted add "$@";;
        *) apk_run add "$@";;
    esac
}
online_simulation() {
    # Each attempt replaces the plan, so a failed direct attempt cannot pollute it.
    package_operation --simulate "$@" > "$WORK/plan" 2>/dev/null
}
simulate() {
    local arg
    : > "$WORK/actions"
    [ -s "$WORK/targets" ] || return 0
    set --
    while IFS= read -r arg; do
        case "$arg" in !*) continue;; esac
        set -- "$@" "$arg"
    done < "$WORK/targets"
    # No --upgrade: existing dependencies change only when constraints require it.
    apk_run query --recursive --fields name "$@" > "$WORK/dependency-query" 2>&1 || block 'Cannot resolve actual package dependencies.'
    sed -n 's/^Name: //p' "$WORK/dependency-query" > "$WORK/dependencies"
    [ -s "$WORK/dependencies" ] || block 'Unknown APK dependency query format.'
    set --
    while IFS= read -r arg; do set -- "$@" "$arg"; done < "$WORK/targets"
    network online_simulation "$@" >/dev/null 2>&1 || block 'APK dependency simulation failed; no packages changed.'
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
    for path in /etc/config/passwall2 /etc/config/passwall2_server /etc/apk/world; do
        [ ! -L "$path" ] || block "Cannot safely maintain a symlink: $path"
    done
    [ "$MODE" = install ] || return 0
    for path in "$KEY_FILE" "$REPO_FILE"; do
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
    network apk_run cache download "$@" >/dev/null 2>&1 || fail 'Package download failed; system untouched.'
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
    if [ "$MODE" = update ]; then
        awk '$0 ~ /^luci-app-passwall2></ {print "luci-app-passwall2"; next} {print}' /etc/apk/world > "$tmp"
        chmod 0644 "$tmp"; mv "$tmp" /etc/apk/world
        return
    fi
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
    [ "$COMMIT" = fix ] || unpin_targets
    [ "$MODE" != install ] || persist_repo
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
                timeout 10 nslookup localhost 127.0.0.1 >/dev/null 2>&1 || fail 'dnsmasq DNS query failed; inspect DHCP/DNS and retained backup.'
            elif [ "$svc" = passwall2 ]; then
                wait_main_processes || fail 'passwall2 has no runtime processes after startup (15 second timeout).'
            else
                [ -n "$(processes "$svc")" ] || fail "$svc has no runtime processes after startup."
            fi
        else
            timeout 60 "/etc/init.d/$svc" stop || fail "Cannot preserve stopped state: $svc"
        fi
    done < "$WORK/services"
    MAINTENANCE=0
    info 'Готово. Конфигурация и состояния служб сохранены.'
    info 'Проверьте DNS и выбранные узлы с клиента LAN: локальные проверки не подтверждают доступность.'
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
    exit "$rc"
}
# The API and asset are always from the official upstream repository.
latest_release() {
    local tag draft prerelease
    fetch "$WORK/release.json" 'https://api.github.com/repos/Openwrt-Passwall/openwrt-passwall2/releases/latest' 2>/dev/null || return 1
    tag=$(jsonfilter -i "$WORK/release.json" -e '@.tag_name' 2>/dev/null || true)
    draft=$(jsonfilter -i "$WORK/release.json" -e '@.draft' 2>/dev/null || true)
    prerelease=$(jsonfilter -i "$WORK/release.json" -e '@.prerelease' 2>/dev/null || true)
    [ "$draft" = false ] && [ "$prerelease" = false ] || return 1
    printf '%s\n' "$tag" | grep -Eq '^[0-9]+([.][0-9]+)*-[0-9]+$' || return 1
    PW_AVAIL=$(printf '%s\n' "$tag" | sed 's/-\([0-9][0-9]*\)$/-r\1/')
    valid_version "$PW_AVAIL" || return 1
    RELEASE_TAG=$tag
}

local_report() {
    local geo_path geo_version
    geo_path=$(uci -q get 'passwall2.@global_app[0].geoview_file' 2>/dev/null || true)
    geo_path=${geo_path:-/usr/bin/geoview}
    geo_version=''
    if [ -x "$geo_path" ]; then
        geo_version=$(timeout 10 "$geo_path" -version 2>/dev/null | awk 'NR==1 && $1=="Geoview" {print $2}')
    fi
    printf '\n  PassWall2\n  Установлено:      %s\n' "${PW_INST:-не установлен}"
    [ "$1" != check ] || printf '  Последний GitHub: %s\n' "${PW_AVAIL:-не удалось проверить}"
    printf '\n  Xray\n  APK-пакет:        %s\n  Рабочий бинарник: %s\n  Путь:             %s\n' \
        "${XR_INST:-не установлен}" "${XR_RUNTIME:-не найден или не распознан}" "$XR_PATH"
    printf '\n  Geoview\n  Версия:           %s\n  Путь:             %s\n' "${geo_version:-не найдена или не распознана}" "$geo_path"
    printf '\n  Xray и Geoview обновляются отдельно через LuCI.\n'
}

check_release() {
    local cmp
    printf '\n============================================================\n'
    printf '                  ПРОВЕРКА ОБНОВЛЕНИЙ\n'
    printf '============================================================\n'
    if ! latest_release; then
        local_report check
        info '  Итог: Не удалось проверить обновления GitHub.'
        [ -n "$PW_INST" ] || info '  PassWall2 не установлен. Выберите пункт 1.'
        return 1
    fi
    local_report check
    if [ -z "$PW_INST" ]; then
        info '  Итог: PassWall2 не установлен. Выберите пункт 1.'
        return 0
    fi
    valid_version "$PW_INST" || fail 'Не удалось сравнить установленную версию PassWall2.'
    cmp=$(compare "$PW_INST" "$PW_AVAIL")
    case "$cmp" in
        '<') printf '%s  Итог: Доступно обновление до %s. Выберите пункт 2.%s\n' "$GREEN" "$PW_AVAIL" "$RESET";;
        '=') info '  Итог: Уже актуально.';;
        '>') printf '%s  Итог: Установленная версия новее GitHub Latest; понижение не предлагается.%s\n' "$YELLOW" "$RESET";;
    esac
}

github_asset() {
    local name index=0 matches=0 selected=0 digest url size actual
    jsonfilter -i "$WORK/release.json" -e '@.assets[*].name' > "$WORK/assets" || block 'Не удалось прочитать список файлов релиза.'
    while IFS= read -r name; do
        if [ "$name" = "$MAIN-$PW_AVAIL.apk" ]; then
            matches=$((matches + 1)); selected=$index
        fi
        index=$((index + 1))
    done < "$WORK/assets"
    [ "$matches" = 1 ] || block 'Подходящий APK отсутствует или найден неоднократно.'
    url=$(jsonfilter -i "$WORK/release.json" -e "@.assets[$selected].browser_download_url" 2>/dev/null || true)
    digest=$(jsonfilter -i "$WORK/release.json" -e "@.assets[$selected].digest" 2>/dev/null || true)
    size=$(jsonfilter -i "$WORK/release.json" -e "@.assets[$selected].size" 2>/dev/null || true)
    [ "$url" = "https://github.com/Openwrt-Passwall/openwrt-passwall2/releases/download/$RELEASE_TAG/$MAIN-$PW_AVAIL.apk" ] || block 'Неожиданный адрес APK.'
    case "$digest" in sha256:*) digest=${digest#sha256:};; *) block 'В релизе нет SHA-256.';; esac
    printf '%s\n' "$digest" | grep -Eq '^[0-9a-f]{64}$' || block 'Некорректный SHA-256 релиза.'
    case "$size" in ''|*[!0-9]*|0) block 'Некорректный размер APK.';; esac
    LOCAL_APK="$WORK/$MAIN-$PW_AVAIL.apk"
    fetch "$LOCAL_APK" "$url" 2>/dev/null || fail 'Не удалось скачать APK с GitHub.'
    actual=$(wc -c < "$LOCAL_APK" | tr -d ' ')
    if [ "$actual" != "$size" ] || [ "$(sha "$LOCAL_APK")" != "$digest" ]; then
        block 'Размер или SHA-256 APK не совпадает.'
    fi
    mkdir -p "$WORK/extracted"
    apk --allow-untrusted extract --destination "$WORK/extracted" "$LOCAL_APK" > "$WORK/extract.log" 2>&1 || block 'Не удалось прочитать проверенный APK.'
    MIN_XRAY=$(sed -n 's/^[[:space:]]*local xray_min_version = "\([0-9.]*\)"[[:space:]]*$/\1/p' "$WORK/extracted/usr/lib/lua/luci/passwall2/util_xray.lua" 2>/dev/null || true)
    valid_version "$MIN_XRAY" || block 'Не удалось определить минимальную версию Xray из APK.'
    valid_version "$XR_RUNTIME" || block 'Не удалось определить рабочую версию Xray; обновите Xray отдельно через LuCI.'
    [ "$(compare "$XR_RUNTIME" "$MIN_XRAY")" != '<' ] || block 'Xray несовместим с новым PassWall2; обновите Xray отдельно через LuCI.'
}

update_plan() {
    local action pkg version count=0 free need size path
    COMMIT=local
    TARGET_PW=$PW_AVAIL
    printf '%s\n' "$LOCAL_APK" > "$WORK/targets"
    apk --no-network list --installed --manifest > "$WORK/installed-before"
    cp /etc/apk/world "$WORK/world-before"
    sha "$XR_PATH" > "$WORK/xray-prepared-hash"
    if ! package_operation --no-network --simulate "$LOCAL_APK" > "$WORK/plan" 2>&1; then
        network online_simulation "$LOCAL_APK" >/dev/null 2>&1 || block 'Не удалось разрешить зависимости через существующие APK-репозитории.'
    fi
    parse_plan "$WORK/plan" > "$WORK/actions" || block 'Неизвестный формат APK-плана.'
    grep -q '^OK:' "$WORK/plan" || block 'Симуляция APK не подтвердила успех.'
    while IFS='|' read -r action pkg version; do
        if [ "$pkg" = "$MAIN" ] && [ "$action" = Upgrading ] && [ "$version" = "$PW_AVAIL" ]; then
            count=$((count + 1))
            continue
        fi
        [ "$action" = Installing ] || block "Небезопасное дополнительное действие APK: $action $pkg"
        case "$pkg" in
            libc|musl*|libubus*|libubox*|libuci|libblobmsg-json*) block "Системная библиотека в плане: $pkg";;
            lib*|lyaml) ;;
            *) block "Небезопасное дополнительное действие APK: $action $pkg";;
        esac
    done < "$WORK/actions"
    [ "$count" = 1 ] || block 'APK не запланировал ровно одно обновление PassWall2.'
    # Extraction measures the expanded package, with room for backup and APK data.
    size=$(du -sk "$WORK/extracted" | awk '{print $1}')
    need=$((size * 2 + 16384))
    while IFS='|' read -r action pkg version; do
        [ "$pkg" != "$MAIN" ] || continue
        size=$(apk query --fields installed-size "$pkg=$version" | awk '/^Installed-Size: / {print $2}')
        case "$size" in ''|*[!0-9]*) block "Не удалось определить размер пакета $pkg.";; esac
        need=$((need + size / 1024 + 1))
    done < "$WORK/actions"
    config_paths > "$WORK/config-paths"
    while IFS= read -r path; do
        size=$(du -sk "$path" | awk '{print $1}')
        need=$((need + size))
    done < "$WORK/config-paths"
    for path in /overlay /tmp; do
        free=$(df -Pk "$path" | awk 'END {print $4}')
        [ "$free" -ge "$need" ] || block "Недостаточно места: $path (нужно $need KiB)."
    done
}

update_passwall() {
    local answer action pkg version cache archive found
    [ -n "$PW_INST" ] || block 'PassWall2 не установлен; выберите установку.'
    valid_version "$PW_INST" || block 'Не удалось определить установленную версию.'
    latest_release || fail 'Не удалось проверить обновления GitHub.'
    case "$(compare "$PW_INST" "$PW_AVAIL")" in
        '=') info 'Уже актуально.'; return;;
        '>') info 'Установленная версия новее GitHub Latest; понижение не выполняется.'; return;;
    esac
    github_asset
    update_plan
    check_paths
    printf '\nПлан обновления PassWall2 (GitHub SHA-256, без подписи feed):\n'
    awk -F '|' '{printf "  %s %s (%s)\n", $1, $2, $3}' "$WORK/actions"
    printf '  Xray: %s (%s), сохранить\n' "$XR_RUNTIME" "$XR_PATH"
    printf '\nПрименить план? [y/N]: '
    read -r answer || return 0
    case "$answer" in y|Y|yes|YES) ;; *) info 'Отменено.'; return;; esac
    set --
    while IFS='|' read -r action pkg version; do
        [ "$pkg" = "$MAIN" ] || set -- "$@" "$pkg=$version"
    done < "$WORK/actions"
    if [ "$#" -gt 0 ]; then
        network apk cache download "$@" >/dev/null 2>&1 || fail 'Не удалось заранее скачать зависимости в APK cache; система не изменена.'
        # --allow-untrusted is needed for the GitHub APK. Verify every matching
        # cached dependency with the normal trusted keys before using that flag.
        cache=/etc/apk/cache
        [ -d "$cache" ] || cache=/var/cache/apk
        while IFS='|' read -r action pkg version; do
            [ "$pkg" != "$MAIN" ] || continue
            found=0
            for archive in "$cache/$pkg-$version."*.apk; do
                [ -f "$archive" ] || continue
                [ ! -L "$archive" ] || block "Ссылка вместо APK в cache: $pkg"
                apk --no-network verify "$archive" >/dev/null 2>&1 || block "Подпись зависимости не подтверждена: $pkg"
                found=1
            done
            [ "$found" = 1 ] || block "Архив зависимости не найден в APK cache: $pkg"
        done < "$WORK/actions"
    fi
    package_operation --no-network --simulate "$LOCAL_APK" > "$WORK/offline-plan" 2>&1 || block 'Автономная симуляция после загрузки не удалась.'
    parse_plan "$WORK/offline-plan" > "$WORK/offline-actions" || block 'Неизвестный формат автономного плана.'
    cmp -s "$WORK/actions" "$WORK/offline-actions" || block 'APK-план изменился после загрузки.'
    apk --no-network list --installed --manifest > "$WORK/installed-now"
    cmp -s "$WORK/installed-before" "$WORK/installed-now" || block 'Состояние APK изменилось; повторите проверку.'
    cmp -s "$WORK/world-before" /etc/apk/world || block 'APK world изменился; повторите проверку.'
    [ "$(sha "$XR_PATH")" = "$(cat "$WORK/xray-prepared-hash")" ] || block 'Бинарник Xray изменился; повторите проверку.'
    check_idle
    backup
    maintenance
    apply
}

main() {
    local command="${1:-}" answer
    [ "$#" = 0 ] || shift
    case "$command" in
        help|-h|--help)
            info 'OpenWrt 25.12.x / APK 3: install, update, check, status, help.'
            info 'Без аргументов в терминале открывается меню.'
            return;;
        check|status|install|update) ;;
        *) fail 'Неизвестная команда; используйте help.';;
    esac
    [ "$#" = 0 ] || fail 'Неизвестные аргументы; используйте help.'
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    require_system
    local_status
    case "$command" in
        status) local_report status; return;;
        check)
            WORK=$(mktemp -d /tmp/passwall2-apk.XXXXXX)
            check_release; return;;
    esac
    [ "$(id -u)" = 0 ] || fail 'Run as root.'
    mkdir /tmp/passwall2-apk-manager.lock 2>/dev/null || fail 'Another manager is running, or a stale manager lock needs review.'
    LOCKED=1
    MODE=$command
    if [ "$command" = update ]; then
        WORK=$(mktemp -d /tmp/passwall2-apk.XXXXXX)
        update_passwall
        return
    fi
    prepare
    select_targets
    simulate
    if [ ! -s "$WORK/actions" ]; then info 'Все необходимые компоненты уже установлены.'; return; fi
    check_paths
    space_check
    printf '\nПлан установки из подписанного APK-репозитория:\n'
    cat "$WORK/plan"
    [ "$DNS_CHANGE" = 0 ] || info 'Внимание: для nftset обычный dnsmasq будет заменён на dnsmasq-full; DHCP-конфигурация будет сохранена.'
    [ "$ADD_FIREWALL" = 0 ] || info 'Будет добавлен отсутствующий firewall.passwall2 include.'
    printf '\nПрименить план? [y/N]: '
    read -r answer || return 0
    case "$answer" in y|Y|yes|YES) ;; *) info 'Отменено.'; return 0;; esac
    prefetch
    backup
    maintenance
    apply
}

menu() {
    local choice action cyan='' bold='' reset=''
    if [ -t 1 ] && [ -z "${NO_COLOR+x}" ]; then
        case "${TERM:-}" in xterm*|screen*|tmux*|linux|rxvt*|vt100|ansi)
            cyan=$(printf '\033[36m'); bold=$(printf '\033[1m'); reset=$(printf '\033[0m');;
        esac
    fi
    trap 'exit 130' INT
    while :; do
        printf '\n%s============================================================\n' "$cyan"
        printf '                      %sPASSWALL2 MANAGER%s%s\n' "$bold" "$reset" "$cyan"
        printf '============================================================%s\n\n' "$reset"
        printf '  ВЫБЕРИТЕ ДЕЙСТВИЕ\n\n------------------------------------------------------------\n\n'
        printf '  1. Установка\n  2. Обновление\n  3. Проверка обновлений\n\n  4. Выход\n\n'
        printf '%s\n' '------------------------------------------------------------'
        printf '  Выбор [1-4]: '
        read -r choice || return 0
        case "$choice" in
            1) action=install;; 2) action=update;; 3) action=check;; 4) return 0;;
            *) info 'Некорректный выбор.'; continue;;
        esac
        if sh "$0" "$action"; then info 'Операция завершена.'; else info 'Операция завершилась с ошибкой.'; fi
        printf '\n  Нажмите Enter для возврата в меню...'
        read -r choice || return 0
    done
}

# Source-only entry is used solely by the isolated offline tests.
if [ "${PW2_SOURCE_ONLY:-0}" != 1 ]; then
    if [ "$#" = 0 ]; then
        if [ -t 0 ] && [ -t 1 ]; then menu; else info 'Для меню нужен терминал; используйте help.'; fi
    else
        main "$@"
    fi
fi
