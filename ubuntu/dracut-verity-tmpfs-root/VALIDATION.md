# Validation scope

[Recipe](README.md) | [Repository index](../../README.md)

## Evidence levels

| Surface | Scope |
| --- | --- |
| Marketplace catalog | Read-only Azure queries on 2026-11-12 returned the pinned non-Pro Ubuntu 24.04 CVM version in westus3, x64/Gen2 and `TrustedLaunchAndConfidentialVmSupported` |
| Offline policy tests | Generalized-source guards, writable-state finalization, pinned input, exact generated boot inputs, required switch-root dependency and root-policy success/rejection paths |
| Real userspace tests | SHA-256 verity format/verification and corrupted-data/wrong-hash rejection; kernel-less Dracut/systemd initramfs and RSA-signed systemd-stub UKI, exact section checks and changed-command-line rejection |
| Pinned attestation SDK | Actual release CLI built from `8774be30f452060c56372d34ccbe34c3d3f3c577` with committed lockfile; PCR-selection/JSON CLI contract checked; wrapper tests request `0,2,4,7,11` and hardware-bound user data, reject incomplete evidence/fallback and do not claim remote verification |
| Complete Marketplace conversion | **Not run**; no source disk/export was acquired in this change |
| Kernel/driver compatibility | Build-time gates implemented; **not exercised against the pinned Marketplace disk** |
| Real live dm-verity corruption | Implemented as a mandatory image-build gate; **not established by the regular-file userspace test** |
| Azure signer enrollment / CVM boot | Instructions and independent-evidence guest verifier supplied; **not run** |
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

## Required before claiming a demonstrated configuration

1. Retain acquisition evidence for the exact unbooted source and its module,
   kernel and package versions. Complete the offline build on an exclusive
   builder and retain all checksums and gate evidence.
2. Publish the actual VHD with its actual public UKI signer in Gallery UEFI db;
   require completed replication and exact signer/template API readback.
3. Boot a supported ConfidentialVM with Secure Boot, vTPM and
   `VMGuestStateOnly`; record the actual Azure image/security profile.
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
