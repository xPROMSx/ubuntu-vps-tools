# Disposable VM integration tests

`CI / Required` requires basic checks and both official amd64 Ubuntu Cloud Image
jobs. Each VM runs the exact checked-out production script with default package
upgrade, repeats with `--no-upgrade`, reboots, verifies a changed boot ID, and
runs the bootstrap again. Missing stages, timeout and unclassified warnings fail.
Images use Canonical's current release image and matching SHA256SUMS over HTTPS;
image URL, digest, commit and accelerator are recorded. This checks current images,
not an immutable image snapshot or a cryptographic signature on the manifest.

QEMU uses KVM when accessible and TCG otherwise. Disks, cloud-init data and VM
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
VM integration. The existing production ShellCheck SC2004 arithmetic-index style
finding is excluded explicitly; all other production findings and guest findings
remain blocking.

Limits: these VMs exercise ext4, QEMU NAT, the supplied kernel and no Pro subscription;
they do not prove provider-specific networking or an interactive FIDO login.
They do verify both required FIDO public identities remain accepted by OpenSSH.
Swap coverage belongs to the subsequent feature PR after this CI stage is approved.
