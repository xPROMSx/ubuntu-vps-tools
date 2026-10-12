"""Fast argument/validation tests: no bootstrap execution or system changes."""
import os
import pathlib
import re
import shlex
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "vps-bootstrap-ubuntu24.sh"
SOURCE = SCRIPT.read_text()


def run(code, success=True, env=None):
    result = subprocess.run(["bash", "-c", code], capture_output=True, text=True,
                            env={**os.environ, **(env or {})}, cwd=ROOT)
    assert (result.returncode == 0) == success, result.stderr
    assert "CI_SECRET_CANARY" not in result.stdout + result.stderr
    return result


for option in ("--help", "-h"):
    assert "Supported: Ubuntu Server 24.04 LTS and Ubuntu Server 26.04 LTS" in run(
        f"bash -x {SCRIPT} {option}", env={"UBUNTU_PRO_TOKEN": "CI_SECRET_CANARY"}).stdout
for args in ("--unknown", "--ssh-port", "--ssh-port ''"):
    run(f"bash {SCRIPT} {args}", success=False)
# Sourcing defines functions only; disable the EXIT cleanup before unit calls.
prefix = f"source {SCRIPT}; trap - EXIT; "
for ports in ("auto", "ssh", "22", "1,65535", "022"):
    run(prefix + f"parse_args --ssh-port {ports}; validate_inputs")
for ports in ("0", "65536", "22,", "-1", "bad"):
    run(prefix + f"parse_args --ssh-port '{ports}'; validate_inputs", success=False)
for value in ("00:00", "23:59", "04:38"):
    run(prefix + f"AUTO_REBOOT_TIME={value}; validate_inputs")
for value in ("24:00", "1:00", "00:60"):
    run(prefix + f"AUTO_REBOOT_TIME={value}; validate_inputs", success=False)
for tz in ("../etc/passwd", "/etc/passwd", "Not/AZone"):
    run(prefix + f"TIMEZONE='{tz}'; validate_inputs", success=False)
run(prefix + "IGNORE_IPS=$'127.0.0.1\\nother'; validate_inputs", success=False)
run(prefix + "parse_args --no-upgrade --autoremove --skip-dns; "
    "[[ $RUN_UPGRADE == 0 && $RUN_AUTOREMOVE == 1 && $CONFIGURE_DNS == 0 ]]")
run(prefix + "parse_args --noswap; [[ $CONFIGURE_SWAP == 0 ]]")
assert "--noswap" in run(f"bash {SCRIPT} --help").stdout
# Isolate OS validation against fixture os-release files, not runner's identity.
check = re.search(r"^check_os\(\) \{\n.*?^\}", SOURCE, re.M | re.S).group()
with tempfile.TemporaryDirectory() as directory:
    fixture = pathlib.Path(directory) / "os-release"
    isolated = check.replace("/etc/os-release", str(fixture))
    for version, codename, supported in (("24.04", "noble", True), ("26.04", "resolute", True),
                                         ("22.04", "jammy", False), ("26.04", "noble", False)):
        fixture.write_text(f"ID=ubuntu\nVERSION_ID={version}\nVERSION_CODENAME={codename}\n")
        run(prefix + isolated + "\ncheck_os", success=supported)
assert not re.search(r"^\s*set\s+-(?:[^\s]*x)", SOURCE, re.M)
print("PASS: help, argument values, OS compatibility and trace/token canary")

# Isolated swap simulations: redirect only this helper's paths to a disposable
# directory. No real mkswap/swapon or host configuration writes are performed.
swap = re.search(r"^configure_swap\(\) \{\n.*?^\}", SOURCE, re.M | re.S).group()
cases = (
    ("create", "", "ok", "1 GiB active; persistent"),
    ("xfs", "FS=xfs", "ok", "1 GiB active; persistent"),
    ("active", "ACTIVE=/dev/zram0", "info", "existing swap preserved"),
    ("noswap", "parse_args --noswap", "info", "skipped by --noswap"),
    ("space", "FREE=4294967295", "warn", "less than 4 GiB free"),
    ("filesystem", "FS=btrfs", "warn", "unsupported filesystem"),
    ("mkswap", "FAIL_MKSWAP=1", "warn", "creation/activation failed"),
    ("swapon", "FAIL_SWAPON=1", "warn", "creation/activation failed"),
    ("inactive", 'printf foreign > "$SWAP_TEST_FILE"', "warn", "existing inactive"),
    ("symlink", 'ln -s "$SWAP_TEST_DIR/foreign" "$SWAP_TEST_FILE"', "warn", "existing inactive"),
    ("fstab", "printf '%s none swap sw 0 0\\n' \"$SWAP_TEST_FILE\" >> \"$SWAP_TEST_FSTAB\"",
     "warn", "fstab entry"),
    ("persistence", "FAIL_BACKUP=1", "warn", "persistence failed"),
    ("active-unlink", "BLOCK_ACTIVE_UNLINK=1", "ok", "1 GiB active; persistent"),
)
with tempfile.TemporaryDirectory() as directory:
    for name, setup, state, detail in cases:
        path = pathlib.Path(directory) / name
        path.mkdir()
        helper = swap.replace("/swapfile", str(path / "swapfile"))
        helper = helper.replace("/etc/fstab", str(path / "fstab"))
        helper = helper.replace("/.swapfile-bootstrap.", str(path / ".swapfile-bootstrap."))
        helper = helper.replace("/etc/.fstab-swap.", str(path / ".fstab-swap."))
        mocks = r'''
FS=ext4 FREE=4294967296 ACTIVE=''
printf '# preserved fstab\n' > "$SWAP_TEST_FSTAB"
printf foreign > "$SWAP_TEST_DIR/foreign"
swapon() {
  if [[ "$*" == '--show=NAME --noheadings' ]]; then printf '%s' "$ACTIVE"; return 0; fi
  printf 'activate\n' >> "$SWAP_TEST_DIR/calls"
  [[ ${FAIL_SWAPON:-0} == 0 ]] || return 1
  ACTIVE=$1
}
findmnt() { printf '%s\n' "$FS"; }
df() { printf 'Avail\n%s\n' "$FREE"; }
fallocate() { printf allocated > "${@: -1}"; }
chown() { :; }
stat() {
  if [[ ${2:-} == '%s:%u:%g:%a' ]]; then printf '1073741824:0:0:600\n';
  else command stat "$@"; fi
}
rm() {
  if [[ ${BLOCK_ACTIVE_UNLINK:-0} == 1 && -n "$ACTIVE" ]]; then
    for file in "$@"; do
      if [[ -e "$file" && "$file" -ef "$ACTIVE" ]]; then return 1; fi
    done
  fi
  command rm "$@"
}
mkswap() { printf 'mkswap\n' >> "$SWAP_TEST_DIR/calls"; [[ ${FAIL_MKSWAP:-0} == 0 ]]; }
backup_file() { [[ ${FAIL_BACKUP:-0} == 0 ]] && cp "$1" "$SWAP_TEST_DIR/backup"; }
'''
        result = run(prefix + helper + mocks + setup + r'''
cp "$SWAP_TEST_FSTAB" "$SWAP_TEST_DIR/before"
configure_swap
[[ ${#FAILED_CHECKS[@]} == 0 ]]
[[ ${SUMMARY_STATE[swap]} == "$SWAP_TEST_STATE" ]]
[[ ${SUMMARY_DETAIL[swap]} == *"$SWAP_TEST_DETAIL"* ]]
final_summary
[[ $(cat "$SWAP_TEST_DIR/foreign") == foreign ]]
if [[ ${SUMMARY_STATE[swap]} == ok ]]; then
  [[ "$ACTIVE" == "$SWAP_TEST_FILE" ]]
  [[ $(command stat -c %h "$SWAP_TEST_FILE") == 1 ]]
  [[ $(grep -Fc "$SWAP_TEST_FILE none swap sw 0 0" "$SWAP_TEST_FSTAB") == 1 ]]
  cp "$SWAP_TEST_FSTAB" "$SWAP_TEST_DIR/after"
  inode=$(command stat -c %i "$SWAP_TEST_FILE")
  configure_swap
  [[ ${SUMMARY_STATE[swap]} == info && $(command stat -c %i "$SWAP_TEST_FILE") == "$inode" ]]
  cmp "$SWAP_TEST_DIR/after" "$SWAP_TEST_FSTAB"
  [[ $(grep -c activate "$SWAP_TEST_DIR/calls") == 1 ]]
else
  cmp "$SWAP_TEST_DIR/before" "$SWAP_TEST_FSTAB"
  [[ ${#WARNINGS[@]} -le 1 ]]
  case "$SWAP_TEST_CASE" in
    active|noswap|space|filesystem|inactive|symlink|fstab) [[ ! -e "$SWAP_TEST_DIR/calls" ]] ;;
  esac
  if [[ "$SWAP_TEST_CASE" == inactive ]]; then [[ $(cat "$SWAP_TEST_FILE") == foreign ]];
  elif [[ "$SWAP_TEST_CASE" == symlink ]]; then [[ -L "$SWAP_TEST_FILE" ]];
  elif [[ "$SWAP_TEST_CASE" == persistence ]]; then [[ -f "$SWAP_TEST_FILE" && "$ACTIVE" == "$SWAP_TEST_FILE" ]];
  else [[ ! -e "$SWAP_TEST_FILE" ]]; fi
fi
[[ -z $(find "$SWAP_TEST_DIR" -name '.swapfile-bootstrap.*' -o -name '.fstab-swap.*') ]]
''', env={"SWAP_TEST_DIR": str(path), "SWAP_TEST_FILE": str(path / "swapfile"),
          "SWAP_TEST_FSTAB": str(path / "fstab"), "SWAP_TEST_STATE": state,
          "SWAP_TEST_DETAIL": detail, "SWAP_TEST_CASE": name})
        token = {"ok": "[✓]", "info": "[i]", "warn": "[!]"}[state]
        assert re.search(re.escape(token) + r" Swap\s+.*" + re.escape(detail), result.stdout)
        assert "RESULT: FAILED" not in result.stdout
        print(f"PASS: swap SIMULATION {name}; summary and non-fatal result")

# Exercise the actual guest log assertions without VM commands or raw-log output.
guest = (ROOT / "tests/ci/guest.sh").read_text()
assertions = re.search(r'python3 - "/root/\$name.log" <<\'PY\'\n(.*?)\nPY', guest, re.S).group(1)
with tempfile.TemporaryDirectory() as directory:
    fixture = pathlib.Path(directory) / "initial.log"
    clean = 'RESULT: SUCCESS\nFull upgrade and autoclean completed\n[✓] Swap  1 GiB active; persistent\n'
    samples = (
        (clean, True, 'PASS: bootstrap result'),
        (clean.replace('RESULT: SUCCESS\n', ''), False, 'success RESULT assertion'),
        (clean + 'FAILED CHECK: redacted fixture\n', False, 'absence of FAILED CHECK assertion'),
        (clean.replace('Full upgrade and autoclean completed', ''), False, 'package upgrade completion assertion'),
        (clean.replace('[✓] Swap  1 GiB active; persistent',
                       '[!] Swap  skipped; creation/activation failed'), False, 'swap initial provisioning assertion'),
        (clean + 'WARN: PRIVATE_DIAG_CANARY\n', False, 'warning allowlist assertion'),
        (clean.replace('[✓] Swap  1 GiB active; persistent',
                       '[!] Swap  PRIVATE_DIAG_CANARY'), False, 'swap initial provisioning assertion'),
    )
    for content, success, expected in samples:
        fixture.write_text(content)
        isolated = ('import subprocess,sys,types; '
                    'subprocess.run=lambda *a,**kw: types.SimpleNamespace(returncode=0,stdout="PRIVATE_DIAG_CANARY"); '
                    f'sys.argv=["fixture",{str(fixture)!r}]; exec({assertions!r})')
        output = run("python3 -c " + shlex.quote(isolated), success=success).stdout
        assert expected in output and 'PRIVATE_DIAG_CANARY' not in output
    print('PASS: guest assertion labels, strict swap success requirement and diagnostic redaction')
