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

## SSH incident reproduction (test-only phase)

The Ubuntu 24.04 VM runs `guest.sh sshid` before the existing installation stages.
`sshid_incident.py` uses a real established SSH control connection and a controlling
PTY to select menu option 2. It sources the exact updater and replaces only `curl`
with a function returning a public identity generated inside the guest. This is
an explicit download fixture, not a test of HTTPS, TLS, retries or the external
key service. Production files are not modified. The separate guest login identity
stays outside managed authorized_keys. Raw PTY output and key material are never
printed or uploaded.

- **SIMULATION:** real systemd stops for 20 seconds, first ssh.service alone with
  ssh.socket active, then both units; the units are restarted. Both production
  detectors run during the window, followed by the real menu operation.
- **REAL APT:** menu option 2 runs alongside an actual openssh-server reinstall,
  without fake package commands, delayed package hooks or injected stop behavior.
  A held fcntl lock establishes package activity at menu submission. This does
  not guarantee that a short real unpack/restart overlaps the later port check;
  diagnostics report the observed result without inventing a failure.
- **RELOAD MEASUREMENT:** 50 concurrent reload/detect pairs per production
  detector per listener architecture, each followed by an immediate post-reload
  detection. Zero failures is a valid finding, not evidence of an observed race.

Diagnostics contain unit states, ports, owner categories, return codes and named
error branches. Endpoints/PIDs, raw stderr, private configuration and full logs
are excluded. Configuration and key files are restored between scenarios and on
exit. All scenarios finish before regression failures are returned: the current
both-units-stopped path is expected to fail the clean-refusal/no-edits contract
and expose traceback/ambiguous wording. The Ubuntu 24.04 job and Required may
therefore be red. Earlier infrastructure failures can prevent these tests from
running; no CI result has been established merely by adding this stage. Existing
installation/rerun/reboot assertions remain mandatory after a successful stage.
Ubuntu 26.04 retains the existing VM sequence.

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
