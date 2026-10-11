"""Fast argument/validation tests: no bootstrap execution or system changes."""
import os
import pathlib
import re
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
