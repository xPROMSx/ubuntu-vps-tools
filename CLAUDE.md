# Claude Code repository guide

## Purpose, environment and file map

Ubuntu VPS Tools initializes and maintains a personal Ubuntu server through one
production entry point, `vps-bootstrap-ubuntu24.sh`. Keep that filename stable.
Support is Ubuntu Server 24.04 LTS (`noble`) and 26.04 LTS (`resolute`), running
as root with a real systemd manager, active OpenSSH socket/service listener,
live kernel/network facilities and network access for APT, public-key retrieval
and configured services. CI uses amd64 official Cloud Images; there is no
production architecture gate, and other architectures/providers are not proven.

| File | Responsibility |
| --- | --- |
| `vps-bootstrap-ubuntu24.sh` | Installer, generated key updater, validations, transactions and final reporting. |
| `README.md` | Project introduction and swap overview. |
| `BASELINE.md` | Immutable provenance of the original historical import. |
| `AGENTS.md` / `CLAUDE.md` | Canonical contributor instructions and this independent Claude guide; maintain both together. |
| `.github/workflows/ci.yml` | Existing Basic checks, two Ubuntu VM jobs and Required aggregation. |
| `tests/ci/README.md` | Coverage contract, diagnostics and limitations. |
| `tests/ci/basic.py` | Isolated CLI/OS, swap, redaction, Pro and reboot regressions. |
| `tests/ci/vm.py` | Official image verification and disposable QEMU orchestration. |
| `tests/ci/guest.sh` | Real installer execution and guest system assertions, plus labelled DNS fault injection. |
| `tests/ci/check_commit_metadata.py` | PR-only public Git metadata policy, not an ownership/security credential verifier. |
| `tests/ci/test_commit_metadata.py` | Standard-library synthetic Git policy regressions, executed by Basic checks. |

Read README.md, BASELINE.md, AGENTS.md and tests/ci/README.md before changing
anything. Before a subsystem edit, read its actual functions/generated files
and matching basic/guest tests. Before changing CI or Git validation, also read
the workflow, VM driver, metadata checker and its synthetic tests. Do not infer
current behavior from the older baseline. Keep both guides consistent.

## Installation sequence and failure boundaries

The Ubuntu contracts below apply to `vps-bootstrap-ubuntu24.sh`.
`openwrt/passwall2-apk-repo.sh` is independent, for OpenWrt 25.12.x / APK 3 /
BusyBox ash. Read `openwrt/README.md`; preserve signed feeds, read-only checks,
separate APK/runtime Xray versions, confirmation, narrow package plans and
configuration/service states. Do not integrate with Ubuntu or run real router
installation from development/CI. Use ash syntax, ShellCheck and the isolated
`tests/openwrt` suite, which does not prove real OpenWrt behavior.
Use the signed APK feed for `install`/ASU recovery, official GitHub Release
APKs with SHA-256 verification for `update`, and GitHub Latest plus local state
for read-only `check`. Keep these paths independent.

The script parses arguments, requires root, validates inputs and the exact OS
version/codename, acquires the existing flock and creates its temporary state
directory. Managed key provisioning, checks and backups precede swap and the
initial no-remove dist-upgrade. Base packages, provider connectivity, timezone
and Pro follow. Refresh APT indexes after Pro and run a no-remove upgrade with
new packages only for newly enabled and confirmed ESM repositories. APT cleanup,
systemd verification, SSH, resolver, sysctl, unattended
updates, UFW and Fail2ban follow. The detailed report and colored summary precede
EXIT cleanup. `FAILED_CHECKS` and the original exit path decide success; the
summary registry is presentation only. Expected warnings remain warnings.

File transactions preserve originals, including symlinks where allowed, until
validation/runtime activation succeeds. SSH rollback uses the same listener
helper. DNS/Fail2ban restart failure returns non-zero and retains recovery
snapshots; it must not claim completion or clear transaction state. APT timer
rollback restores captured unit state. UFW's independent activation guard is
not a replacement for ordinary file rollback. Do not generalize this into a
new transaction framework or erase recovery state to make a fixture pass.

**Never run a real installation on the development host or a live server
without explicit authorization.** It changes root access and system state.
Syntax checking, `--help` and isolated function mocks are safe validation paths.
Full execution belongs only in the existing disposable guest workflow. Do not
weaken safety checks, run host firewall/network commands or reboot a runner.

## Access and subsystem invariants

Root authorized_keys is exclusively owned by the updater: validated public data
from the configured SSH ID source, one mandatory personal Ed25519 identity and
two mandatory FIDO identities. Unknown/provider keys are replaced with console
recovery backups. Preserve root ownership/modes, symlink checks, flock, atomic
same-filesystem rename and deduplication by type+blob, with the personal identity
preferred over a dynamic duplicate. The generated local updater must retain the
same behavior on every refresh. Never execute remotely downloaded installer code.

SSH requires key-only root login, no password/keyboard-interactive/empty-password
authentication or X11 forwarding, and only `.ssh/authorized_keys` in
AuthorizedKeysFile. `sshd -t` and effective base/root/current-or-conservative
contexts are authority, not grep of raw config. Preserve listener ports and
socket/service activation. Reload an active ssh.service under socket activation;
leave an inactive socket-activated service inactive. No listener is a safe failure.
The read-only authority audit warns about effective command/CA authorities and
hostbased/GSSAPI alternatives with AuthenticationMethods=any. It does not disable
provider mechanisms. Source locations are hints; same-file Match notices and
source-scan limitations are informational. PAM alone is not a security finding.
Exclusive managed file contents do not imply exclusive server-wide access.

DNS uses the managed systemd-resolved global DoT configuration and uncached
checks. Preserve link/VPN routes and restore prior resolver files/symlinks when
validation fails. Global DoT need not override more-specific routing domains.
Keep incomplete restart recovery explicit. The narrowly owned temporary IPv6
fallback is cleaned on EXIT; never remove administrator routes.

Active UFW is audited and preserved, with failed validation still a failed
check. Inactive activation requires safe existing files, conservatively detected
live SSH context, IPv4/IPv6 SSH allows, deny incoming/allow outgoing and a
transient rollback guard. Preserve routed policy and foreign firewall owners.
Fail2ban keeps sshd in normal mode with systemd backend/nftables multiport action.
Recidive stays disabled; stop only a surviving legacy recidive jail, not other
jails. Do not add replacements or assume the external application jails exist.

The initial no-remove dist-upgrade precedes base packages and Pro. A second
no-remove upgrade with new packages runs only when this run enabled and confirmed
ESM repositories. Index refreshes and autoclean remain enabled with --no-upgrade.
Autoremove is opt-in, runs at most once after base packages and required upgrades,
including with --no-upgrade. Keep unattended
updates and update/security/ESM origins, persistent APT timers at 02:30/02:50
Europe/Moscow with no randomized delay, default timezone Europe/Moscow and the
04:38 unattended reboot time. Package-triggered reboot policy remains separate
from the installer's opt-in immediate post-install scheduling.

Normal Ubuntu Pro setup checks attachment, can attach with a supplied token and
enable available ESM/Livepatch services. No token on an unattached system is an
optional warning; unavailable services and actual failures remain distinct.
`--nopro` skips setup without a prompt, token use or subscription changes. It
does not remove ubuntu-pro-client, change repositories or stop read-only final
status reporting. Never trace tokens or expose token attachment files.

Ubuntu Pro token input occurs once after root/input/OS validation, before bootstrap
work. Keep inherited tokens shell-local from startup; never export them to early
APT children. Empty/EOF input skips attachment without a later prompt. Pro status,
attachment and service activation stay after the initial upgrade/base packages;
already attached systems never reattach even with a pre-collected token.

Any active swap, regardless of type/size/location, is preserved untouched. Only
ext4/XFS with at least 4 GiB free can receive a new 1 GiB root:root 0600
`/swapfile`. Existing inactive files, symlinks or fstab entries need manual review.
Use allocated blocks and no-overwrite publication, unlink the temporary hard
link before swapon, retain device/inode identity for owned-file failure cleanup,
and back up fstab before an atomic single-entry append. Errors are non-fatal;
retain active swap if persistence fails, report the limitation, never swapoff
and do not change swappiness.

## Flags and reboot

With no arguments and TTY stdin/stdout, a one-shot menu selects the existing
Pro/reboot flags before system changes; exit/EOF performs no installation.
Any argument bypasses it. Non-interactive no-argument runs still start directly.

`--help`/`-h` show usage. `--ssh-port PORT` selects Fail2ban ports (default auto),
not sshd listeners. `--no-upgrade` skips both package upgrades, not base
installation, index refreshes or Pro; `--autoremove` opts into removal once after
base installation and upgrades. `--skip-dns` preserves resolver configuration.
`--noswap` skips
automatic swap creation and changes no existing swap/fstab. `--nopro` is an
informational Pro skip. Argument order is arbitrary.

No installer reboot is automatic by default. `--reboot` requests a 30-second
transient systemd timer only after successful main/final_report and complete
EXIT cleanup, with reboot-required present and no errors/signals/failed checks,
pending transaction/recovery or remaining IPv6 guard. Preserve an already active
fixed-name timer; a systemd-run failure is explicit and non-zero, with no direct
reboot. No persistent unit/cron/session-bound background process is added.
Test this flag only with mocks, not new real runner/guest reboot scenarios.

## Testing and confidentiality

Run `bash -n` for the tracked shell scripts, then
`shellcheck --exclude=SC2004 vps-bootstrap-ubuntu24.sh`,
`shellcheck tests/ci/guest.sh`, `python3 tests/ci/basic.py`,
`python3 tests/ci/test_commit_metadata.py` and `git diff --check`.
Keep the known production SC2004 style exclusion; other findings block CI.
Fast tests simulate failure/idempotence, Pro skipping, diagnostic redaction and
reboot scheduling; they do not replace system integration.

QEMU jobs SHA256-check official current Ubuntu 24.04/26.04 amd64 images, use KVM
when available or TCG otherwise, execute exact HEAD with the default full upgrade,
validate real SSH/DoT/kernel/UFW/Fail2ban/update/swap state, rerun, reboot and rerun.
The DNS recovery scenario is labelled fault simulation with real restoration.
The guest-generated private test key never leaves the disposable disk; its
guest-only AuthorizedKeysCommand is an explicit expected authority warning and
the key is excluded from managed authorized_keys. Only sanitized diagnostics
are uploaded. Full bootstrap logs/configurations and private keys are not artifacts.
CI does not prove provider networking, XFS swap activation, paid Pro attachment
or real FIDO authentication; accepted FIDO public keys are not proof of a login.

Do not put operational addresses/allowlists, private emails, credentials,
subscription data or SSH bodies in documentation, PR descriptions or test output.
Public SSH keys are intentional public material, not leaked private keys, but
should not be duplicated here. Discuss existing privacy concerns privately with
the maintainer; do not silently delete production defaults or attempt an
incomplete secret cleanup without a separate authorized rotation decision.

## History, identity and PR boundaries

BASELINE.md, `baseline/known-good-2026-09-29` and `baseline-2026-09-29` are immutable
historical provenance, not a claim that main is still byte-identical to the first
import. Source repositories ShadowRocket-Rules and 3x-ui-pro are read-only.
Use feature branches and reviewed PRs against main. Do not merge/release, change
settings/rulesets, force-push or rewrite published commits implicitly.

Set local Git identity before commits, never global configuration. Maintainer
display names are Proms or xPROMSx with the exact agreed public noreply address
in `tests/ci/check_commit_metadata.py`. Other accepted identities are standard
GitHub noreply users and narrowly matched web/bot identities. A matching name
alone never permits a private email. Review author, committer, messages and
attribution trailers; preserve honest attribution and exclude private contacts.
Email syntax does not cryptographically authenticate the person or bot.

PR metadata validation uses trusted full base/head SHAs, complete commit history
and at most 1000 new commits; commits reachable from base are not revalidated.
Missing/shallow history fails safely and rejected values never enter failure
messages. Push events explicitly skip historical metadata validation; synthetic
policy tests still run. Basic checks, OpenWrt offline checks and applicable VM
jobs feed `CI / Required`. Only OpenWrt-only PRs and related documentation may
skip VMs through the scope rule; Ubuntu/shared test/scope-rule/workflow changes
and main pushes require both VMs. Require applicable checks to pass for review;
do not assume GitHub Settings
currently list those contexts as protected required checks, or change settings.
