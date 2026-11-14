# Disposable VM integration tests

`CI / Required` requires basic checks, OpenWrt offline checks and both official
amd64 Ubuntu Cloud Image jobs when applicable. OpenWrt-only PRs and related
documentation can skip both VMs; Ubuntu/shared test/scope-rule/workflow changes
and every main push require them. The aggregator accepts a skipped VM only when
the basic job explicitly reported the OpenWrt-only scope. The scope rule has
isolated regressions in `tests/openwrt/test_ci_scope.py`.
Each VM runs the exact checked-out production script with default package
upgrade, repeats with `--no-upgrade`, reboots, verifies a changed boot ID, and
runs the bootstrap again. Missing stages, timeout and unclassified warnings fail.
Images use Canonical's current release image and matching SHA256SUMS over HTTPS;
image URL, digest, commit and accelerator are recorded. This checks current images,
not an immutable image snapshot or a cryptographic signature on the manifest.

QEMU always uses KVM when accessible, with an explicit warning on fallback to
TCG. CI applies the published KVM udev rule to grant the ordinary runner user
device access. Apart from installing VM tooling, this device permission rule is
the only runner configuration change; firewall, network and services stay intact.
Disks, cloud-init data and VM
processes are temporary. No runner firewall/systemd/network reconfiguration,
external secrets, paid services or production VPS are needed. Installed runner
packages are VM tooling only.

The test identity is generated exclusively inside the guest. A guest-only
AuthorizedKeysCommand returns its public key, so production managed authorized_keys
remains untouched by test keys. This is intentionally an additional test authority:
the expected audit warning is explicitly classified, not treated as a clean audit.
SSH uses the guest's private key through QEMU's host-forwarded port, including
bootstrap invocation itself. Private keys and full bootstrap logs remain inside
the disposable disk and are never artifacts. Only assertion/state diagnostics
are uploaded; the failure reporter does not dump journal, tokens or key bodies.

`guest.sh recovery` is a labelled fault simulation: failed resolver queries are
injected while the real production DNS transaction restores actual guest files
and restarts the service. It is not presented as a network outage integration test.
The fast Python suite isolates argument/OS validation; it does not claim to replace
VM integration. Fast tests also simulate swap failures/idempotence, Pro skipping,
reboot scheduling and diagnostic redaction. The existing production ShellCheck
SC2004 arithmetic-index style finding is excluded explicitly; all other production findings and guest findings
remain blocking.

## SSH incident reproduction and reliability

The Ubuntu 24.04 VM runs `guest.sh sshid` before the existing installation stages.
`sshid_incident.py` uses a real established SSH control connection and a controlling
PTY to select menu option 2. It sources the exact updater and replaces only `curl`
with a function returning a public identity generated inside the guest. This is
an explicit download fixture, not a test of HTTPS, TLS, retries or the external
key service. The harness does not rewrite production source. The separate guest login identity
stays outside managed authorized_keys. Raw PTY output and key material are never
printed or uploaded.

- **SIMULATION:** real systemd stops for 20 seconds, first ssh.service alone with
  ssh.socket active, then both units; the units are restarted. Both production
  detectors run during the window, followed by the real menu operation. Both
  cases require success with updated keys and disabled passwords after recovery.
- **SIMULATION TIMEOUT:** both units stay stopped; the guest test shell overrides
  SYSTEM_WAIT_SECONDS to two seconds after sourcing the unchanged production
  script. The default production window is unchanged. Expect exit 1, unchanged
  keys/configuration, the SSH-not-started guidance and no traceback. Units are
  started only for explicit simulation cleanup, before the independent APT test.
- **REAL APT:** menu option 2 runs alongside an actual openssh-server reinstall,
  without fake package commands, delayed package hooks or injected stop behavior.
  A held fcntl lock establishes package activity at menu submission. This does
  not guarantee that a short real unpack/restart overlaps the later port check;
  diagnostics report the observed result without inventing a failure. An observer
  started after PTY fork samples both unit states at 0, 5, 15 and 30 seconds after
  APT exits, even while the menu waits. It never starts units. If units return,
  the operation must succeed; no automatic return and a refusal are reported as
  a HARNESS failure, rather than accepting the reinstall scenario as a success.
- **RELOAD MEASUREMENT:** 50 concurrent reload/detect pairs per production
  detector per listener architecture, each followed by an immediate post-reload
  detection. Separate during-failures and after-failures are reported for each
  detector. During failures are diagnostic only. Only after failures following
  successful reload commands count as production regressions; failed reload
  commands remain separately classified as harness failures. Zero after failures
  is a valid finding, not evidence of an observed post-reload defect.
- **CALM:** option 2 must succeed on settled units before the interruption tests.

Diagnostics contain unit states, ports, owner categories, return codes and named
error branches. Endpoints/PIDs, raw stderr, private configuration and full logs
are excluded. Configuration and key files are restored between scenarios and on
exit. All scenarios finish before production regression failures are returned.
Production regressions have explicit `FAIL: PRODUCTION scenario=... expected=...`
labels. Infrastructure failures use `FAIL: HARNESS` with a safe exception message,
type and source location. Nonzero reload commands are counted and do not truncate
the 50 measurements. A runtime directory is prepared before the outcome query;
sampling still reports a real missing-directory failure without hiding it.
Earlier infrastructure failures can prevent scenarios from running. Existing
installation/rerun/reboot assertions remain mandatory after a successful stage.
Ubuntu 26.04 retains the existing VM sequence.

The maintainer-provided first CI result reproduced the listener failure only in
the both-units-stop simulation. Service-only stop succeeded with a systemd-owned
listener. Same-version reinstall held a real dpkg lock but did not reproduce the
failure. cgroup2 warnings were confined to the connection query. The old harness
then aborted without completed reload statistics; that abort is not reproduction.
New CI results must establish completion after these harness/production changes.

`test_sshid_reliability.py` checks parser status separation, bounded retries,
real fcntl contention in a temporary directory, cloud-init/unit command doubles,
wait/timeout/signals, recovery summaries and TTY/NO_COLOR behavior offline.
`test_sshid_incident.py` checks diagnostic redaction and error classification.

Limits: these VMs exercise ext4, QEMU NAT, the supplied kernel and no Pro subscription;
they do not prove provider-specific networking or an interactive FIDO login.
They do verify both required FIDO public identities remain accepted by OpenSSH.
Real swap checks require a 1 GiB active root:root 0600 file, one fstab entry and
the same inode on rerun/reboot. XFS and negative creation paths use isolated
simulations; these are not presented as real XFS activation tests.

Basic checks also run `check_commit_metadata.py` and `test_commit_metadata.py`.
The checker validates author/committer only in the trusted full PR base..head
range, with complete history and a 1000-new-commit limit. Missing objects/shallow
history fail PR validation. Maintainer public identities and narrow GitHub
noreply/web/bot formats are accepted; rejected values are not printed. This is
a privacy convention, not proof of account ownership. Push events explicitly
skip historical metadata validation. Tests use synthetic repositories/identities.
For a local range check, use full commit SHAs:

```bash
python3 tests/ci/check_commit_metadata.py --event pull_request \
  --base "$BASE_SHA" --head "$HEAD_SHA"
```

The check feeds the existing Required aggregation; it does not configure GitHub
branch protection or change the VM matrix.

`OpenWrt offline checks` runs BusyBox ash syntax, ShellCheck and `tests/openwrt`
with rewritten temporary paths and command doubles. It does not install APK
packages on the runner or validate signatures, router networking or ASU recovery.
See `openwrt/README.md` for the implementation's evidence and validation limits.
