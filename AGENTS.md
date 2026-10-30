# Contributor and agent guidance

## Scope and execution boundary

This repository initializes and maintains a personal Ubuntu VPS. The stable,
single-file production entry point is `vps-bootstrap-ubuntu24.sh`; preserve its
filename and keep testing complexity outside it. Read README.md, BASELINE.md,
this guide, CLAUDE.md and tests/ci/README.md before making changes.

Supported releases are Ubuntu Server 24.04 LTS (`noble`) and 26.04 LTS
(`resolute`). Real installation requires root, a running systemd manager,
an active OpenSSH socket/service listener, live kernel/network facilities and
network access for APT, public-key retrieval and the configured services.
CI proves amd64 Cloud Image behavior; the script has no architecture gate and
CI does not establish support for other architectures or provider setups.

**Production-sensitive:** the installer changes SSH access, packages, DNS,
firewall, timers and other system state. Never run its installation path on the
development host or a live server without explicit authorization. Syntax checks,
`--help` and isolated test mocks are safe validation paths. Real integration runs
belong only in the existing disposable QEMU guests. Never remove safety checks
or relax mandatory VM assertions to make a test pass.

## Runtime contracts to preserve

The Ubuntu contracts below apply to `vps-bootstrap-ubuntu24.sh`. The independent
OpenWrt entry point is `openwrt/passwall2-apk-repo.sh` for OpenWrt 25.12.x / APK 3
and BusyBox ash. Read `openwrt/README.md` before changing it. Never run a real
router install/update from development or CI. Preserve signed feeds, read-only
checks, actual Xray runtime handling, explicit confirmation, narrow package
plans and saved configuration/service state; do not integrate with Ubuntu.
Validate with ash syntax, ShellCheck and `tests/openwrt` offline regressions.
These mocks do not prove real OpenWrt behavior.
Use the signed APK feed for `install`/ASU recovery, official GitHub Release
APKs with SHA-256 verification for `update`, and GitHub Latest plus local state
for read-only `check`. Keep these paths independent.

- Root `/root/.ssh/authorized_keys` is exclusively managed: validated keys from
  the configured SSH ID source, one mandatory personal Ed25519 identity and two
  mandatory FIDO identities. Unknown/provider keys are replaced, with recovery
  backups. The generated `/usr/local/sbin/update-sshid-proms` uses locking,
  symlink protection, same-filesystem atomic rename and type+blob deduplication;
  the explicit personal identity takes priority over a dynamic duplicate.
  Download public data, never execute a remote key-installation script.
- SSH policy requires public keys, forbids password/keyboard-interactive/empty
  passwords and X11 forwarding, allows root key-only login, and requires only
  `.ssh/authorized_keys` in AuthorizedKeysFile. Validate `sshd -t` and effective
  `sshd -T`/root contexts, including the actual connection or conservative fallback.
  Preserve ports, listener architecture and unrelated configuration. Reload an
  active ssh.service even with ssh.socket active; do not manually start an
  inactive socket-activated service. No active listener is a safe failure.
- The SSH authority audit is read-only. Effective command/CA authorities or
  hostbased/GSSAPI alternatives under AuthenticationMethods=any produce warnings,
  not automatic disabling. Source lines are hints, not proof of active policy.
  Same-file conditional Match notices and unavailable source diagnostics are
  informational; neither PAM alone nor inactive source settings prove a finding.
  Managed file ownership does not prove exclusive server-wide authentication.
- systemd-resolved uses the managed global DNS-over-TLS drop-in and uncached
  resolution checks. Preserve link/VPN DNS routes. Restore original resolver
  files/symlinks on validation failure; failed runtime restart retains recovery
  state instead of claiming a completed rollback. `--skip-dns` skips this setup.
- UFW discovers live SSH ports/current context conservatively. An active
  administrator firewall is audited without rewriting rules; failed validation
  remains a failed check. Inactive activation requires safe existing rule files,
  IPv4/IPv6 SSH allows, deny-incoming/allow-outgoing policy and the independent
  rollback guard. Preserve routed policy and foreign firewall owners.
- Fail2ban keeps the sshd jail in normal mode with systemd backend and nftables
  multiport action. Recidive is explicitly disabled; a surviving legacy recidive
  jail is stopped individually. Do not stop foreign jails or add recidive replacements.
- Ubuntu Pro is optional when unattached and no token is supplied. Normal setup
  checks attachment and available ESM/Livepatch services; it can attach using a
  supplied token and enable supported services. `--nopro` skips setup and ignores
  the token without changing the subscription; base package installation and
  read-only final status reporting remain. Never log or trace credentials.
- After key provisioning/checks/backups and swap, APT normally runs a no-remove
  dist-upgrade before base packages and Pro setup. Refresh indexes after Pro;
  run a no-remove upgrade with new packages only if ESM repositories were newly
  enabled and confirmed during this run. Autoclean follows package work;
  autoremove runs at most once and only by explicit opt-in, including with
  --no-upgrade. Unattended upgrades remain
  enabled with configured update/security/ESM origins; APT timers run at 02:30
  and 02:50 Europe/Moscow, persistent and without randomized delay. The default
  timezone is Europe/Moscow and unattended reboot time is 04:38. This package
  reboot policy is separate from the installer’s opt-in `--reboot`.
- Swap creation is optional/non-fatal: any active swap is preserved untouched.
  Otherwise only ext4/XFS with at least 4 GiB free can receive a 1 GiB root:root
  0600 `/swapfile`. Existing inactive files, symlinks and fstab entries are not
  repaired automatically. Prepare allocated blocks, publish without overwriting,
  unlink the temporary hard link before activation and track device/inode for
  owned-file cleanup. Back up fstab and add one persistent entry atomically.
  Persistence failure retains activated swap and warns; never swapoff or tune
  swappiness. `--noswap` makes no swap/fstab changes.
- TCP Fast Open and UDP buffers are configured; BBR/fq is conditional on kernel
  support. Temporary IPv6 fallback is narrowly owned and cleaned on EXIT; never
  remove administrator routes or extend this into general networking management.
- Transactions preserve original files/symlinks until validation succeeds.
  Incomplete recovery retains snapshots. `FAILED_CHECKS` and the existing exit
  path determine failure; summary state is presentation only. Warnings do not
  become failures merely because the colored summary displays them.
- `--reboot` schedules one transient timer after successful main/final_report
  and EXIT cleanup, only with reboot-required, no errors/signals/failed checks,
  pending transaction/recovery or remaining IPv6 guard. An active fixed-name
  timer is preserved. Scheduling failure returns non-zero with no immediate
  reboot. No automatic installer reboot occurs without the flag. Test it only
  with mocks; do not use it to reboot a runner or add VM reboot scenarios.

Ubuntu Pro token input occurs once after root/input/OS validation, before bootstrap
work. Keep inherited tokens shell-local from startup; never export them to early
APT children. Empty/EOF input skips attachment without a later prompt. Pro status,
attachment and service activation stay after the initial upgrade/base packages;
already attached systems never reattach even with a pre-collected token.

APT update activation captures all five units, including the unattended-upgrades
shutdown helper, and removes persistent/runtime masks before enabling it.
The existing apt-timers transaction verifies exact enabled/active state on
rollback, preserves running APT workers, and retains recovery files on failure.

## CLI and subsystem reading

With no arguments and TTY stdin/stdout, a one-shot menu selects the existing
Pro/reboot flags before system changes; exit/EOF performs no installation.
Any argument bypasses it. Non-interactive no-argument runs still start directly.

| Option | Contract |
| --- | --- |
| `--help`, `-h` | Usage only. |
| `--ssh-port PORT` | Fail2ban port selection, not sshd listener reconfiguration; default auto. |
| `--no-upgrade` | Skip both package upgrades, not base packages, index refreshes or Pro. |
| `--autoremove` | Explicit package-removal opt-in once after base packages and upgrades. |
| `--skip-dns` | Preserve existing resolver configuration. |
| `--noswap` | Skip automatic swap creation; preserve existing swap. |
| `--nopro` | Informational Pro setup skip, no token prompt or attachment changes. |
| `--reboot` | Request the gated 30-second transient reboot timer. |

Read the relevant functions and their generated configuration/updater before any
subsystem edit. For SSH, firewall, DNS, packages/Pro/timers, swap or rollback,
also read `tests/ci/basic.py`, `tests/ci/guest.sh` and the CI coverage document;
for CI changes read `.github/workflows/ci.yml` and `tests/ci/vm.py` as well.
Keep AGENTS.md and CLAUDE.md technically consistent in the same PR.

## Validation and privacy

Run from the repository root:

```bash
bash -n vps-bootstrap-ubuntu24.sh
bash -n tests/ci/guest.sh
shellcheck --exclude=SC2004 vps-bootstrap-ubuntu24.sh
shellcheck tests/ci/guest.sh
python3 tests/ci/basic.py
python3 tests/ci/test_commit_metadata.py
git diff --check
```

Basic tests isolate CLI/OS validation, swap failure/idempotence simulations,
diagnostic redaction, Pro skipping and mocked reboot scheduling. They are not
system integration. Existing QEMU jobs checksum-verify official current Ubuntu
24.04/26.04 amd64 images, run the exact PR HEAD with full upgrade, validate real
SSH/DoT/kernel/UFW/Fail2ban/update/swap state, rerun, reboot and rerun again.
DNS rollback injection is labelled simulation with real file/service recovery.
KVM is used when available, otherwise TCG. These tests do not prove provider
networking, XFS activation, paid Pro attachment or real FIDO authentication.
Guest-only test SSH authority is an expected audit warning; its key is not added
to managed authorized_keys. Only sanitized diagnostics are artifacts.

Keep tokens, private keys, operational addresses/allowlists, subscription data
and raw private configurations out of commits, docs, PR bodies and test logs.
Public SSH keys are not private keys, but do not duplicate their bodies here.
Report existing confidentiality concerns privately; changing production values
or rotating a discovered credential needs a separate authorized decision.

## Git and historical references

Use main plus a feature branch and reviewed PR; no implicit merge, release,
settings changes, force push or published-history rewrite. Preserve BASELINE.md,
`baseline/known-good-2026-09-29` and `baseline-2026-09-29` as immutable historical
references. They describe the original import, not the evolving main script.
The source repositories ShadowRocket-Rules and 3x-ui-pro are strictly read-only.

Set identity locally before committing, never globally. Maintainer names are
`Proms` or `xPROMSx` with the exact approved public GitHub noreply address in
`tests/ci/check_commit_metadata.py`. Other contributors use accepted public
GitHub noreply identities; conventional web/bot identities have narrow rules.
Review author, committer, commit messages and attribution trailers before push;
do not fabricate attribution or expose private contact details. Address syntax
is a privacy convention, not cryptographic account ownership or authentication.

The checker validates only trusted full base/head SHA PR ranges (maximum 1000
new commits), fails on shallow/missing history and redacts rejected identity
values. Push events intentionally skip historical identity validation. Basic
checks invoke its synthetic regression suite. Existing `CI / Required` depends
on Basic checks, OpenWrt offline checks and both VM jobs when applicable. Only
OpenWrt-only PRs and related documentation may skip VMs through the scope rule;
Ubuntu/shared test/scope-rule/workflow changes and main pushes require VMs.
Passing it is a contributor requirement, not
a claim that repository protection currently enforces those status contexts.
