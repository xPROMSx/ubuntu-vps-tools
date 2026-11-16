#!/bin/sh
# Exercise local APK files against a disposable, unprivileged APK database.
# Usage: APK=/path/to/apk sh tests/openwrt/apk3-local-integration.sh
set -eu

APK=${APK:-apk}
"$APK" --version | grep -q 'apk-tools 3\.0\.5' || {
    echo 'This experiment requires apk-tools 3.0.5.' >&2
    exit 1
}
work=$(mktemp -d "${TMPDIR:-/tmp}/passwall-apk3-test.XXXXXX")
trap 'rm -rf "$work"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$work/root/cache" "$work/app/usr/share/apk3-test-app" "$work/dep/usr/share/apk3-test-dep"
printf 'app\n' > "$work/app/usr/share/apk3-test-app/data"
printf 'dependency\n' > "$work/dep/usr/share/apk3-test-dep/data"

"$APK" mkpkg -I name:apk3-test-dep -I version:1.0-r0 -I arch:noarch \
    -I description:fixture -I license:MIT -F "$work/dep" -o "$work/dep.apk"
for version in 1.0-r0 2.0-r0 3.0-r0; do
    "$APK" mkpkg -I name:apk3-test-app -I "version:$version" -I arch:noarch \
        -I description:fixture -I license:MIT -I depends:apk3-test-dep \
        -F "$work/app" -o "$work/app-$version.apk"
done

run_apk() {
    "$APK" --root "$work/root" --cache-dir cache --allow-untrusted --no-network "$@"
}
run_apk add --initdb --usermode
cp "$work/root/etc/apk/world" "$work/world-empty"
if run_apk add --simulate "$work/app-1.0-r0.apk" > "$work/missing.log" 2>&1; then
    echo 'A missing dependency unexpectedly passed simulation.' >&2
    exit 1
fi
grep -q apk3-test-dep "$work/missing.log"
cmp "$work/world-empty" "$work/root/etc/apk/world"

run_apk add "$work/dep.apk"
grep '^apk3-test-dep><' "$work/root/etc/apk/world" > "$work/foreign-entry"
cp "$work/root/etc/apk/world" "$work/world-before"
run_apk add --simulate "$work/app-1.0-r0.apk" > "$work/plan"
grep -q 'Installing apk3-test-app (1.0-r0)' "$work/plan"
cmp "$work/world-before" "$work/root/etc/apk/world"
run_apk add "$work/app-1.0-r0.apk"
grep '^apk3-test-app><' "$work/root/etc/apk/world" > "$work/first-entry"

run_apk add --simulate "$work/app-2.0-r0.apk" > "$work/plan"
grep -q 'Upgrading apk3-test-app (1.0-r0 -> 2.0-r0)' "$work/plan"
grep -Fxq "$(cat "$work/first-entry")" "$work/root/etc/apk/world"
run_apk add "$work/app-2.0-r0.apk"
grep '^apk3-test-app><' "$work/root/etc/apk/world" > "$work/second-entry"
if cmp -s "$work/first-entry" "$work/second-entry"; then
    echo 'The identity constraint did not change after the local upgrade.' >&2
    exit 1
fi
grep -Fxq "$(cat "$work/foreign-entry")" "$work/root/etc/apk/world"

# Test a narrowly scoped identity-pin normalization and a subsequent upgrade.
awk '/^apk3-test-app></ {print "apk3-test-app"; next} {print}' \
    "$work/root/etc/apk/world" > "$work/world-normalized"
cp "$work/world-normalized" "$work/root/etc/apk/world"
run_apk add --simulate "$work/app-3.0-r0.apk" > "$work/plan"
grep -q 'Upgrading apk3-test-app (2.0-r0 -> 3.0-r0)' "$work/plan"
run_apk add "$work/app-3.0-r0.apk"
grep -Fxq "$(cat "$work/foreign-entry")" "$work/root/etc/apk/world"
grep -q '^apk3-test-app><' "$work/root/etc/apk/world"
printf '%s\n' 'APK 3.0.5 local-file simulation, dependency and world checks passed.'
