# Ubuntu 24.04 CVM: Dracut/systemd verity root with a tmpfs overlay

[All configurations](../../README.md) | [Validation scope](VALIDATION.md)

This independent reference implementation replaces Canonical's snap-bootstrap
initramfs/manifest flow with **Dracut and systemd**. It starts from an unbooted
Azure Ubuntu CVM Marketplace disk, finalizes a disposable root filesystem,
generates dm-verity data, and builds a signed UKI embedding `roothash=` and
explicit data/hash partition identifiers. All writes to the effective root are
volatile; the verified ext4 lower never has a writable fallback.

**Status: experimental, PR-ready implementation with local component tests.
Conversion of the pinned Marketplace disk and Azure CVM boot have not been
demonstrated.** A catalog query is not boot qualification. Do not replace a
working boot image or claim production integrity based on these tests.

## Inputs and boundaries

| Input | Requirement |
| --- | --- |
| Marketplace | `Canonical:ubuntu-24_04-lts:cvm:24.04.202610280` |
| Catalog check | 2026-11-12, westus3; newest non-Pro `cvm` version returned |
| Catalog metadata | x64, Gen2, `TrustedLaunchAndConfidentialVmSupported`; no purchase plan |
| Target userspace | Ubuntu 24.04, systemd 255 family, Dracut 060 family |
| Kernel | Explicit version from the source UKI, with the **same source module tree** |
| Signer | Caller-owned RSA certificate and private key, kept outside the checkout |
| Builder | Exclusive Ubuntu 24.04 x86-64 Linux machine with root, mounts, loops and dm-verity |

The pin is in [inputs.json](inputs.json). No kernel version is guessed from the
builder's `uname -r`. The build requires the source's
`EFI/ubuntu/kernel.efi-<kernel>` and `/lib/modules/<kernel>`, rejects unsigned
loadable driver dependencies, and extracts the original kernel's `.linux`
section. A missing stock UKI, changed layout, missing module or unsupported
source stops the build; it does not substitute the builder kernel.

The source root label must be `cloudimg-rootfs`, ESP label `UEFI`; an optional
separate boot partition must have label `BOOT`. The filesystem must be readable
ext4, not a booted or encrypted/provisioned OS volume. Additional persistent
fstab mounts are rejected. The converter records the input hash but **cannot
authenticate a local raw image's Marketplace origin**: retain authorized
acquisition evidence independently.

No source disk, export, VM, storage, Gallery, role assignment, or Azure resource
is created by [build-image.sh](build-image.sh). There is no deployment controller
and no call into the existing Canonical producer. The commands below that use
Azure are manual, optional, billable operations in your own authorized scope.

## Boot and trust chain

```text
Unbooted Marketplace raw disk (read-only)
  -> new GPT disk; copy root and optional separate /boot
  -> offline customization + guest packages + matching-kernel Dracut initramfs
  -> unmount root, check ext4, veritysetup format + verify
  -> ukify with original kernel, new initramfs, generated signed .cmdline
  -> write signed UKI to ESP only, verify root again, corruption checks
  -> fixed Azure VHD

Firmware Secure Boot db -> signed EFI/BOOT/BOOTX64.EFI (systemd-stub UKI)
  -> systemd-veritysetup-generator -> read-only /dev/mapper/root
  -> /sysroot ext4 ro,noload -> systemd.volatile=overlay (tmpfs upper/work)
  -> required initramfs root-policy guard -> switch-root -> writable volatile /
```

The new disk has a 512 MiB ESP, 32 GiB ext4 data partition, and a separately
sized SHA-256 verity hash partition. Data/hash PARTUUIDs are generated per build.
The signed command line contains:

```text
root=/dev/mapper/root rootfstype=ext4 ro rootflags=ro,noload
roothash=<generated SHA-256 digest>
systemd.verity_root_data=/dev/disk/by-partuuid/<data UUID>
systemd.verity_root_hash=/dev/disk/by-partuuid/<hash UUID>
systemd.verity_root_options=panic-on-corruption
systemd.volatile=overlay
```

Root-device auto-discovery and initrd fstab processing are disabled. A required
unit blocks `initrd-switch-root.service` unless the effective root is a writable
overlay with upper/work on the same tmpfs, an ext4 read-only lower is mounted
from the healthy verity mapper, and no swap is active. Corruption must not be
ignored. A second guard checks the effective root during normal system startup.
There is no GRUB, shim, unsigned command-line add-on, Canonical manifest or
stock-initramfs fallback on the new ESP. Firmware must support the standard
Gen2 removable-media path; this still needs Azure acceptance.

The root fstab disk mounts and disk swap are removed before hashing; the ESP is
not mounted in the guest. Files from a separate `/boot` are copied inside the
verified lower. Root growth, Azure resource-disk formatting/swap and persistent
journals are disabled. Machine IDs and SSH host keys are cleared before hashing.
Cloud-init provisions the normal Ubuntu user, public key and agent state into
the volatile upper. Reboots regenerate guest identity and host keys: plan SSH
host verification accordingly. No fixed passwordless-sudo test account is baked
into the lower by this recipe.

## 1. Obtain and record the unbooted source

Use an authorized export of the exact pin. The following are **read-only**
catalog commands, not resource creation:

```bash
az vm image list --location westus3 --publisher Canonical \
  --offer ubuntu-24_04-lts --sku cvm --all --query '[].urn' -o tsv
az vm image show --location westus3 \
  --urn Canonical:ubuntu-24_04-lts:cvm:24.04.202610280
```

For acquisition/export guidance see steps 1-2 of the existing
[local-builder workflow](../verity-tmpfs-root/LOCAL-BUILD.md). Use **this** image
URN, a new owned resource group and a `VMGuestStateOnly` source disk; do not use
the old recipe's image pin, geometry assertion, producer, signing scripts or
deployment functions. Export permissions and organization policies still
apply. Export SAS tokens are credentials: never print, commit or retain them;
revoke access after download. If export is prohibited, stop.

Keep the source and output outside the checkout. Convert a downloaded VHD:

```bash
qemu-img convert -f vpc -O raw /secure/input/source.vhd /secure/input/source.raw
```

Never pass the VHD footer as part of a raw disk. Never boot the source, and never
attach it to the acceptance VM. On an exclusive builder, inspect the raw disk
read-only to determine the actual source kernel:

```bash
sudo losetup --find --show --read-only --partscan /secure/input/source.raw
# Record the returned device, for example /dev/loop7.
lsblk -o PATH,LABEL,FSTYPE /dev/loop7
sudo mkdir -p /secure/input/esp
# Replace this with the partition that lsblk actually identifies as UEFI.
sudo mount -o ro /dev/loop7p15 /secure/input/esp
find /secure/input/esp/EFI/ubuntu -maxdepth 1 -name 'kernel.efi-*' -print
sudo umount /secure/input/esp
sudo losetup --detach /dev/loop7
```

The partition number above is an example, **not a pinned geometry**. Select the
exact kernel suffix present in the source and retain the source module/package
inventory. If multiple kernels are present, deliberately select one with its
matching modules; do not silently select the latest file.

## 2. Build offline

Install builder dependencies on the **exclusive build machine**, not on the
machine hosting unrelated workloads:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  python3 binutils openssl sbsigntool systemd-ukify qemu-utils \
  rsync gdisk dosfstools e2fsprogs cryptsetup-bin util-linux udev kmod
```

The build chroots into a **new copy** of the source and installs `dracut-core`,
`systemd-boot-efi` and `cryptsetup-bin` there. It disables package service startup
and uses private `/run` plus slave `/dev` and `/sys` bind mounts. It neither
installs the target kernel on the host nor imports host kernel modules.
Package repositories need outbound access; the resulting complete package
inventory is evidence. Repository versions are not snapshot-pinned, so byte
reproducibility and arbitrary future package versions are not claimed.

Budget at least 150 GiB plus package headroom. The 32 GiB data partition is
fixed for this configuration; a source that does not fit fails during copy.
The corruption test makes a disposable data-partition copy. Reserve ample
tmpfs RAM in the eventual CVM; do not add disk swap to compensate.

Generate your own signer, or use an existing approved RSA signer:

```bash
umask 077
mkdir -m 700 /secure/signer
openssl req -new -x509 -newkey rsa:3072 -sha256 -nodes \
  -keyout /secure/signer/db.key -out /secure/signer/db.pem \
  -days 365 -subj '/CN=Ubuntu Dracut CVM reference/'
chmod 600 /secure/signer/db.key

cd ubuntu/dracut-verity-tmpfs-root
sudo bash build-image.sh /secure/input/source.raw \
  /secure/signer/db.key /secure/signer/db.pem \
  /secure/runs/dracut-example '<exact-source-kernel-version>'
```

The key stays outside the guest and output image. Existing run directories,
in-checkout signing inputs/output directories, provisioned sources and
mismatched/expired certificates are rejected. The source image is opened
read-only. Do not run concurrent builds or build on shared/production hosts.

Success requires `DRACUT_VERITY_IMAGE_BUILD_PASSED` and produces:

- `image.raw`, Azure fixed `image.vhd`, `cvm.efi` and public `signer.der`;
- original kernel payload, new initramfs and exact generated `cmdline`;
- `evidence/build.json` with kernel, root hash and both PARTUUIDs;
- source/output hashes, source/output GPT layout, package and signed-driver
  inventories, initrd inventory, verity format, ext4 and fixed-VHD checks;
- offline corruption rejection plus live verified-read I/O failure/status `C`.

The signed UKI's `.linux`, `.initrd` and `.cmdline` are checked byte-for-byte
against inputs; `sbverify` checks the certificate signature. The protected root
is never mounted writable after hashing. The image is verified again after the
ESP write. Failed runs retain diagnostics but are **not publishable**; cleanup
only artifacts you own, after confirming mounts and mappings were released.

## 3. Publish and enroll the actual signer

Use a new private Azure Compute Gallery and your owned upload resources. See the
existing [local workflow, publication steps 4-5](../verity-tmpfs-root/LOCAL-BUILD.md)
and [Gallery certificate-profile helper](../verity-tmpfs-root/scripts/custom-uki/New-GallerySecurityProfile.ps1)
for the Azure API shape, but use **this build's** VHD and `signer.der`.
Do not run the old end-to-end deployment controller: it builds the old variant.

Required publication and deployment gates:

1. Verify the build marker, output checksums and independent source acquisition
   record. Upload the fixed VHD privately using your authorized identity.
2. Create a Gen2 x64 Linux Gallery definition with
   `TrustedLaunchAndConfidentialVmSupported`; publish the image version with a
   UEFI db profile containing the **exact public certificate** from this build.
   Retain `MicrosoftUefiCertificateAuthorityTemplate` as in the existing helper.
3. Require completed replication and exact signer/template readback with
   `$expand=ReplicationStatus,UefiSettings`. An accepted PUT or a certificate
   merely stored in the image is not enrollment.
4. Deploy one supported DCas_v6 **ConfidentialVM**, with Secure Boot and vTPM
   enabled and `VMGuestStateOnly`. Do not use Trusted Launch or disable Secure
   Boot to make a failed UKI boot. Pass your own SSH public key.
5. Retain Azure `az vm show` evidence of the actual Gallery version and
   `securityProfile`. Confirm capacity/quota before allocating.

Removing the Canonical initramfs also removes its disk-unlock/provisioning
integration. This reference uses an unencrypted verified OS disk and relies on
`VMGuestStateOnly` for guest state. It does **not** support
`DiskWithVMGuestState`, Canonical's FDE key-unlock flow, or claim compatibility
with every CVM family. Preserve appropriate image licensing and export policy.

## 4. Accept the deployed CVM, including a real reboot

Copy [verify-guest.py](verify-guest.py), independently retained
`evidence/build.json`, and `signer.der` to the guest using your approved channel.
Use the **local build record**, not hashes supplied only by the running guest.

```bash
sudo python3 verify-guest.py build.json signer.der > first-boot.json
# Retrieve first-boot.json outside the guest, including its bootId.
sudo systemctl reboot
# Reconnect; copy verifier/build record/certificate again (the root is volatile).
sudo python3 verify-guest.py build.json signer.der \
  --previous-boot-id '<bootId-from-first-boot.json>' > reboot.json
```

First-boot checks include firmware Secure Boot/SetupMode/db signer, TPM, exact
kernel and boot arguments, the live verity table's hash/devices, full offline
verity verification, tmpfs/root policy, cloud-init, guest agent and failed
systemd units. It requires at least 128 MiB of measured tmpfs block allocation
and 2,050 new inodes from 128 MiB plus 2,048 small files and reboot markers.
Reboot acceptance requires a distinct boot ID and loss of the entire workload.
Keep both JSON results and Azure security-profile evidence outside the VM.
Guest checks cannot establish Azure CVM security type by themselves.

Deallocate or remove only your own test VM and temporary upload/source
resources after acceptance. There is no automatic resource cleanup controller.

## Threat model and limits

Secure Boot authenticates the UKI and its embedded root hash, identifiers,
kernel and initramfs; dm-verity checks the lower's data blocks. A malicious
hash tree or partition-table edit cannot produce data matching the signed hash
but can deny boot. Failures must not trigger raw-root recovery.

The tmpfs upper is writable and can shadow executable/configuration files:
**this does not make the effective root immutable**. Privileged guest attackers,
other trusted firmware db signers, alternate signed images and old signed UKIs
are outside this limited enforcement claim. There is no rollback counter,
production root-hash attestation/PCR policy, FDE, or disk confidentiality
guarantee. Secure Boot enrollment and Azure first-boot/reboot evidence remain
mandatory before claiming a working CVM reference.

## Local tests

```bash
python3 tests/test_recipe.py
# On a disposable Ubuntu 24.04 test machine:
sudo apt-get install -y --no-install-recommends \
  dracut-core systemd-boot-efi systemd-ukify cryptsetup-bin sbsigntool
sudo python3 tests/test_components.py
```

The component test creates a real **kernel-less** Dracut/systemd initramfs,
packages a synthetic kernel payload in a real signed UKI, checks all sections
and rejects a changed expected command line. It also formats/verifies actual
verity files and rejects corruption. It does not boot a synthetic or real CVM.
Missing tools are test failures, not skipped checks. CI runs this configuration
separately on Ubuntu 24.04 without Azure resource creation.
