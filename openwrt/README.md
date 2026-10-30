# PassWall2 APK Manager

The standalone script supports OpenWrt 25.12.x, APK 3, BusyBox ash and
firewall4. It is derived from version 4.0.3 of
[xPROMSx/ShadowRocket-Rules/passwall2-apk-repo.sh](https://github.com/xPROMSx/ShadowRocket-Rules/blob/e690dfcaaee2bfc80e4ba1ab4643aba49aa899aa/passwall2-apk-repo.sh)
and retains GPL-3.0-only; see [COPYING](COPYING). The original repository is
read-only. Follow the [download instructions](../README.md#openwrt-passwall2-apk-manager)
to download to a temporary file, check ash syntax and then replace the local
`/root/passwall2-apk-repo.sh`; downloading does not execute it.

Run `sh /root/passwall2-apk-repo.sh` in an SSH terminal for the menu. Select
1 to install or restore missing components, 2 to update PassWall2, 3 to
check updates, or 4 to exit. After an operation, cancellation or error press
Enter to return to the menu. EOF exits. Without a terminal and without arguments
nothing is installed. The direct commands are `install`, `update`, `check`,
`status` and `help`; both changing operations require confirmation. No automatic
yes option is provided. Releases are stable only.

First run `sh /root/passwall2-apk-repo.sh check`, inspect the installed version,
actual Xray path and latest release, and then use `update` to review its plan.
Checking queries only GitHub Latest and local state, does not download APKs,
refresh APK indexes, change packages or services, or contact SourceForge. If
GitHub fails the local status is still printed and the result is unknown.
`status` uses no network. An installed version newer than Latest is not downgraded.

## Sources and package plans

`install` uses `openwrt-passwall-build` for the firmware's architecture. Its
key is SHA-256 pinned to
`52802b143489214e13b78f96599a147a638205cc22d9dd6d71229504e38ddc00`;
APK verifies the signed feed and archives. The inherited key pin and live feed
still require owner verification on a router. No `--allow-untrusted` is used
for installation. An existing PassWall2 package is not downgraded or reinstalled.
Missing Geoview, transparent proxy components and, if needed, Xray are restored.
The actual APK dependency plan is checked; localizations, extra proxy cores and
unsafe system changes are rejected. A compatible Xray runtime is preserved even
if its APK record is older.

Upstream requires dnsmasq with nftset for firewall4. If the existing running
variant lacks nftset, installation proposes replacing it with dnsmasq-full,
shows the APK plan and explicitly warns before confirmation. DHCP configuration
and service state are saved and restored, and a previously running dnsmasq must
be running afterwards and answer a local `localhost` query on 127.0.0.1. An
absent, stopped or ambiguous DNS setup is refused for manual review. A custom
setup that does not listen on loopback can fail this check and requires review.
No DNS replacement is attempted by `update`.

`update` uses only the official Openwrt-Passwall/openwrt-passwall2 GitHub Latest
release. It selects the single matching main APK and verifies its size and
SHA-256 `digest` from release metadata before extraction or installation. The
local GitHub APK uses `apk add --allow-untrusted`; a GitHub digest is not an
independent developer signature and is not equivalent to the signed feed. The
APK's actual Xray minimum is inspected without executing package code. If the
configured binary is too old or its version cannot be established, update Xray
separately in LuCI first.

APK simulates the downloaded local file. The plan permits only the PassWall2
upgrade and installation of needed non-system libraries; it rejects removal,
downgrades, DNS changes, other cores, Geoview or Xray changes and kernel-module
changes. Required libraries must be resolvable through existing repositories
and preloaded into the APK cache; their signatures are verified with normal
trusted keys before a second, matching offline simulation. A plan that cannot
be staged safely is refused. Already installed dependencies
need no SourceForge access. No global upgrade or forced dependency override is
used. APK 3 writes an identity hash for a local APK into `world`; after success
the manager normalizes only the PassWall2 identity entry and preserves other
constraints. The next local APK replaces the previous identity constraint.

Xray and Geoview are independently updated by PassWall2, not this manager. Xray
App Update may replace the configured executable without updating `xray-core`
APK metadata, so the two versions can legitimately differ. The check reports
both, along with the configured binary paths and Geoview version when available.
For network reads the script tries direct HTTPS first and falls back only to an
explicit `PROXY=http://...` setting; no proxy address is built in.

## Preservation and verification

Changing operations require root, a supported platform, a lock, a reviewed plan,
free space and explicit confirmation. Archives are staged before stopping the
service. One APK commit is made without retry or automatic package rollback.
UCI configuration, DHCP, firewall, user lists, geodata and service enable/running
states are backed up and restored without replacing updated application code.
A saved enabled and configured main proxy is restored after ASU removed the
package and init script; a fresh or inactive configuration is not started.
The server service is never enabled solely from saved configuration. Existing
service state is preserved. For an active main service process checks allow up
to 15 seconds; no old logs are treated as new failures.

On failure, `/root/passwall2-apk-backup` and the printed temporary evidence path
are retained. They can contain private data; do not publish them. Inspect the
actual package, DNS and service state before retrying, and move the backup aside
only after preserving any needed recovery data. Successful local checks cannot
prove LAN DNS, firewall routing or node connectivity. The owner should first
run `check` on a real OpenWrt router, inspect the update plan, and authorize an
actual installation separately.

## Tests

```sh
busybox ash -n openwrt/passwall2-apk-repo.sh
shellcheck -s sh openwrt/passwall2-apk-repo.sh
python3 -m unittest discover -s tests/openwrt -p 'test_*.py'
APK=/path/to/apk-3.0.5 sh tests/openwrt/apk3-local-integration.sh
git diff --check
```

The Python tests use isolated command doubles, not a router or real signatures.
The separate APK 3.0.5 test uses synthetic packages and a disposable unprivileged
database; it covers simulation, missing dependencies, identity pins and successive
local upgrades. Neither test establishes behavior on a real OpenWrt installation.
