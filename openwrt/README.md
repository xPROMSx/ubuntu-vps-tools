# PassWall2 APK Manager notes

The standalone script is derived from version 4.0.3 of
[xPROMSx/ShadowRocket-Rules/passwall2-apk-repo.sh](https://github.com/xPROMSx/ShadowRocket-Rules/blob/e690dfcaaee2bfc80e4ba1ab4643aba49aa899aa/passwall2-apk-repo.sh).
It retains the source's GPL-3.0-only license; see [COPYING](COPYING).
The source repository is read-only.

## Feed and package evidence

The feed is `openwrt-passwall-build`, under
`releases/packages-25.12/<DISTRIB_ARCH>/{passwall_packages,passwall_luci,passwall2}/packages.adb`.
The key URL remains `https://master.dl.sourceforge.net/project/openwrt-passwall-build/apk.pub`.
Its retained SHA256 pin is
`52802b143489214e13b78f96599a147a638205cc22d9dd6d71229504e38ddc00`.
A mismatch blocks use of the feed. **Development network policy returned
CONNECT 403 for SourceForge: the live key and signed indexes could not be
revalidated during import.** The inherited pin is not a newly verified key.
There is no unsigned bypass.

The GitHub release APK `26.10.29-r1` was inspected as packaging evidence. Its
19 direct dependencies include coreutils/base64/nohup/timeout, curl, geoview,
ip-full, libc, libuci-lua, lua, luci-compat, luci-lib-jsonc, luci-lua-runtime,
lyaml, resolveip, tcping, unzip and both v2ray geodata packages. It does not
directly require Sing-box, HAProxy, Shadowsocks or languages. This is evidence
about the release asset, not proof of the live SourceForge artifact. At runtime
APK resolves the actual feed dependencies and simulates the plan; excluded
components and unsafe system changes block before installation.

Installation additionally requests only missing chinadns-ng, dnsmasq-full and
nft socket/tproxy/nat modules, following the upstream firewall4 build selection.
Basic dnsmasq replacement uses an explicit conflict constraint in the same APK
operation; DHCP and existing UCI files are backed up and preserved. `update`
never performs this variant replacement.
On a fresh installation, an absent `firewall.passwall2` include is added for
upstream firewall reload integration and shown in the confirmation plan. An
existing section and all unrelated firewall/DNS settings are preserved; vendor
changes to dnsmasq.localuse and uhttpd limits are not retained.

## Xray requirements

Upstream sources reviewed:

- [api.lua](https://github.com/Openwrt-Passwall/openwrt-passwall2/blob/26.10.29-1/luci-app-passwall2/luasrc/passwall2/api.lua): `get_app_path` reads `global_app.xray_file`; App Update replaces that binary.
- [com.lua](https://github.com/Openwrt-Passwall/openwrt-passwall2/blob/26.10.29-1/luci-app-passwall2/luasrc/passwall2/com.lua): default path `/usr/bin/xray`.
- [util_xray.lua](https://github.com/Openwrt-Passwall/openwrt-passwall2/blob/26.10.29-1/luci-app-passwall2/luasrc/passwall2/util_xray.lua): generated configurations declare `version.min`, currently `26.7.11`.
- [Makefile](https://github.com/Openwrt-Passwall/openwrt-passwall2/blob/26.10.29-1/luci-app-passwall2/Makefile): dependencies and optional build selections.

The script reads `xray_min_version` from the signature-verified candidate APK
without executing its code. It does not hardcode a minimum or use release-table
bundle versions. An unknown declaration blocks a change. `install` with an
already installed older PassWall2 preserves that package and uses its local
compatibility declaration to restore missing parts. Custom binary paths can be
read and a suitable binary preserved; automatic repair requires a regular
`/usr/bin/xray`. If APK dependencies demand replacing a suitable App Update
binary merely to satisfy old APK metadata, the plan is refused.

A missing binary whose APK is current can be repaired with one targeted
`apk fix --reinstall xray-core` operation in `install`. This cannot be combined
with a PassWall2 upgrade or installation of other missing components; a mixed
damaged state requires manual review, then a new check.

## APK 3 and failure handling

The [OpenWrt 25.12 APK Makefile](https://github.com/openwrt/openwrt/blob/openwrt-25.12/package/system/apk/Makefile)
selects APK **3.0.5**, commit `b5a31c0d865342ad80be10d68f1bb3d3ad9b0866`.
Its [manuals and source](https://github.com/alpinelinux/apk-tools/tree/v3.0.5)
were checked for add/fix/query/fetch/extract/cache, repository precedence,
simulation output and commit flags. In particular:

- `add` replaces specified world constraints. No `--upgrade` is used, so existing
  dependencies change only when required. Exact target pins are removed after
  success by editing only target entries, preserving unrelated constraints.
  No second APK commit is used to remove pins.
- `--repositories-file` replaces the normal file and repositories.d search.
  Effective original feeds are assembled with `/etc` overrides of `/lib`, then
  only PassWall feed entries are replaced. Checks keep everything temporary.
- `extract` verifies the signature and extracts into a temporary directory,
  without executing scripts. The compatibility declaration is read as text.
- `cache download` fills a dedicated cache. Offline simulation happens after
  staging: APK 3 can hide uncached packages from its offline solver even in
  simulation. Reviewed and offline action lists must match.

A local APK 3.0.5 build was exercised with temporary packages signed by a
disposable test key, confirming query field names, manifest/policy format,
simulation and cache commands. This is not an OpenWrt installation test.

Archives are downloaded before service maintenance. OpenWrt postinst can start
init scripts, so inert PassWall settings are used briefly during the single
commit. Existing UCI files, user rule lists and geodata are restored; shipped
application code is not restored over the update. Prior enable and running
states are restored individually. The server is never started or enabled
merely because this is `install`.

Postchecks verify package/runtime versions, startup exit status and process
presence. They do not scan historical logs or prove LAN DNS, firewall behavior
or remote node reachability. The owner must check real OpenWrt behavior,
especially dnsmasq replacement and postinst effects after ASU.

Success removes temporary backups/logs. Failure retains
`/root/passwall2-apk-backup` and the printed temporary evidence path. These
contain private router data: do not publish them. Move the retained backup
aside before another real change. Commits are never retried, package rollback
is not automatic, and unconfirmed service recovery remains a failure.
Before removing a stale manager directory lock, verify its process has exited.

## Offline validation

```sh
busybox ash -n openwrt/passwall2-apk-repo.sh
shellcheck -s sh openwrt/passwall2-apk-repo.sh
python3 -m unittest discover -s tests/openwrt -p 'test_*.py'
```

Tests run ash with rewritten fixture paths and command doubles. They cover
summaries, absent components, no-op/update selection, newer runtime preservation,
unsafe/optional-package rejection, configuration/service preservation, network
and API errors, prerelease policy, direct/proxy reads, staging failure and a
failed commit without retry. They do not test actual signatures, the live feed,
kernel modules or router connectivity. CI never installs APK packages on the
Ubuntu runner; Ubuntu VM behavior stays in the existing suite.
