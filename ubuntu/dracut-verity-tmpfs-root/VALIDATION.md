# Validation scope

[Recipe](README.md) | [Repository index](../../README.md)

## Evidence levels

| Surface | Scope |
| --- | --- |
| Marketplace catalog | Read-only Azure queries on 2026-11-12 returned the pinned non-Pro Ubuntu 24.04 CVM version in westus3, x64/Gen2 and `TrustedLaunchAndConfidentialVmSupported` |
| Offline policy tests | 14 tests passed: generalized-source guards, writable-state finalization, pinned input, exact generated boot inputs, private mount-propagation enforcement, temporary resolver permissions/exact restoration, required switch-root dependency, volatile-service override and root-policy success/rejection paths |
| Real userspace tests | SHA-256 verity format/verification and corrupted-data/wrong-hash rejection; kernel-less Dracut/systemd initramfs and RSA-signed systemd-stub UKI, exact section checks and changed-command-line rejection |
| Pinned attestation SDK | Actual release CLI built from `8774be30f452060c56372d34ccbe34c3d3f3c577` with committed lockfile; PCR-selection/JSON CLI contract checked; wrapper tests request `0,2,4,7,11` and hardware-bound user data, reject incomplete evidence/fallback and do not claim remote verification |
| Complete Marketplace conversion | **Passed on 2026-11-12** using an unbooted disk from the exact Marketplace pin on a dedicated Ubuntu 24.04 Azure builder; complete build marker includes fixed-VHD validation |
| Kernel/driver compatibility | Matching signed-module gates passed for original kernel `7.0.0-1016-azure-fde`; source configuration includes `CONFIG_INTEL_TDX_GUEST=y`; actual CVM runtime compatibility remains unestablished |
| Real live dm-verity corruption | **Passed in the complete image build**: disposable corrupted data rejected both offline verification and live mapper reads, with mapper status `C` |
| Visible volatile-overlay backing | **Passed on the Azure builder** using the original image's read-only verity mapper: moved ext4 lower, retained tmpfs upper/work and both setup/runtime guard invocations passed |
| Azure signer enrollment / CVM boot | Gallery publication, completed replication and exact custom signer/Microsoft template readback passed. `VMGuestStateOnly` reached the original kernel with Secure Boot enabled, verity and native overlay but failed the root guard. Both initial and corrected-image `NonPersistedTPM` attempts reported Compute `SecureBootClientError`; guest firmware db verification remains unperformed |
| First boot / workload / real reboot | Guest acceptance gates supplied; **not run** |
| Actual PCR-11 quote / MAA flow | SSH-invoked collector installed before hashing; **not run on a CVM**; verified MAA PCR-11 value emission and expected-UKI remote verification are not established |

These tests are not simulated evidence of Azure firmware behavior. A UKI with
a synthetic kernel payload proves packaging/signature/section binding only.
The Dracut userspace test excludes kernel modules explicitly; the real build
requires matching signed source modules and an original kernel payload.

The build's success marker is emitted only after root finalization, matching
driver inspection, initramfs inventory checks, ext4/verity checks, signature and
exact signed-section checks, post-ESP verity verification, disposable corruption
offline/live-read rejection and fixed-VHD geometry/footer checks. It does not
mean firmware enrollment or Azure acceptance passed.

The actual build exposed two builder-integration issues: a restrictive umask
made the temporary resolver unreadable by APT's sandbox user, and shared mount
propagation leaked a data-root mount into host-service namespaces. The producer
now uses a temporary mode-0644 resolver, restores the original file or symlink
before hashing, fails on package-index download errors, and requires private
build-directory mount propagation. Focused regressions and the subsequent
complete Marketplace build passed with these fixes.

The initial CVM boot also exposed a native systemd overlay-layout mismatch:
systemd overmounted the ext4 root at the same target and detached the tmpfs
backing mount, so the guard could not independently inspect the effective root
and upper/work filesystems. The custom `systemd-volatile-root.service` setup now
moves the verified lower and retains the backing tmpfs at visible paths. The
real-image builder probe, both actual Dracut/signed-UKI component tests, and a
new complete Marketplace conversion passed with this change. The corrected
image passed signed-section, live-corruption and fixed-VHD gates; these are not
a successful CVM boot.

The stateless attempt used the same originally published image. Its Azure
Compute VM instance-view reported
`ProvisioningState/failed/SecureBootClientError`, not an MAA token error.
Neither serial nor screenshot diagnostics were available (HTTP 404), so this
does not establish the precise rejected component, failed SKR, or a general
incompatibility between custom UEFI certificates and `NonPersistedTPM`.
The corrected image was published as Gallery version 1.0.1 with the unchanged
public signer and Microsoft template. Independently retained output hashes and
root hash matched the uploaded VHD and verified UKI. A distinct VM using this
version, `NonPersistedTPM`, Secure Boot/vTPM and managed boot diagnostics enabled
at creation reported the same Secure Boot error within about 20 seconds. Both
diagnostic blobs were again unavailable. The failed target was verified
deallocated; stateless boot, guest firmware db, real reboot and MAA qualification
remain blocked.

A further publication experiment explicitly set
`publishingProfile.targetRegions[].encryption.osDiskImage.securityProfile.confidentialVMEncryptionType`
to `NonPersistedTPM`, supported by Gallery API version 2024-03-03. Azure rejected
this regional profile under the existing
`TrustedLaunchAndConfidentialVmSupported` definition, requiring the
`ConfidentialVM` feature. A separate `ConfidentialVM` definition then rejected
custom `securityProfile.uefiSettings`, stating that UEFI customization is only
supported for `TrustedLaunchSupported`, `ConfidentialVmSupported` or
`TrustedLaunchAndConfidentialVmSupported`. No image with this explicit profile
was successfully published. These API results establish the tested
configuration constraints, not a universal claim that every stateless custom-key
boot mechanism is unsupported.

Operational evidence retrieval also exposed stale instance-view output when
managed Run Command names were reused after deletion. The independent upload
hash gate rejected the old build records before publication; they were
quarantined and retrieved again using unique image-scoped command names. Do not
accept a successful PUT or an old execution result as evidence of a new command.

## Required before claiming a demonstrated configuration

1. Retain acquisition evidence for the exact unbooted source and its module,
   kernel and package versions. Complete the offline build on an exclusive
   builder and retain all checksums and gate evidence.
2. Publish the actual VHD with its actual public UKI signer in Gallery UEFI db;
   require completed replication and exact signer/template API readback.
3. Boot a supported ConfidentialVM with Secure Boot, vTPM and
   `NonPersistedTPM`; record the actual Azure image/security profile. Do not
   substitute `VMGuestStateOnly`: it persists encrypted VMGS, although neither
   mode here requests confidential OS-disk encryption.
4. Run the guest verifier using independently retained build inputs. Confirm
   firmware db and live kernel/hash/device identities, healthy read-only verity
   lower, writable tmpfs overlay, no swap, usable agents and cloud-init.
5. Save first-boot evidence outside the guest, perform a real reboot, and require
   a different boot ID and disappearance of the measured tmpfs workload.
6. Qualify firmware removable-media boot behavior, kernel CVM hardware support,
   failure behavior under corruption and deployment cleanup in that environment.
7. Run the installed `cvm-attest` over SSH with a relying-party challenge and an
   explicit approved MAA endpoint. Retain the quote, PCR values, TEE evidence,
   event log and returned token. Require independent verification of token,
   AK/quote/TEE binding, freshness and expected full UKI measurements before
   calling the expected root hash attested. A PCR-11 coverage claim is not the
   PCR-11 digest; user-data binds the TEE report, not quote `extraData`.

No production-complete root-hash attestation, rollback protection, immutable
effective-root executable policy, OS-disk encryption or general CVM-family
compatibility is claimed. Existing Ubuntu 22.04 or Azure Linux experiments do
not supply missing evidence for this independent Ubuntu 24.04 boot chain.
