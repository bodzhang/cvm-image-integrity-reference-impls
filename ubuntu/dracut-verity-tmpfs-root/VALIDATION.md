# Validation scope

[Recipe](README.md) | [Repository index](../../README.md)

## Evidence levels

| Surface | Scope |
| --- | --- |
| Marketplace catalog | Read-only Azure queries on 2026-11-12 returned the pinned non-Pro Ubuntu 24.04 CVM version in westus3, x64/Gen2 and `TrustedLaunchAndConfidentialVmSupported` |
| Offline policy tests | 15 tests passed: generalized-source guards, writable-state finalization, pinned input, exact generated boot inputs, private mount-propagation enforcement, temporary resolver permissions/exact restoration, required switch-root dependency, volatile-service override, shared-parent setup and root-policy success/rejection paths |
| Guest-verifier tests | 3 tests passed: completed clean cloud-init, explicit recoverable-warning reporting, and fatal/incomplete/malformed-status rejection |
| Real userspace tests | SHA-256 verity format/verification and corrupted-data/wrong-hash rejection; kernel-less Dracut/systemd initramfs and RSA-signed systemd-stub UKI, exact section checks and changed-command-line rejection |
| Pinned attestation SDK | Actual release CLI built from `8774be30f452060c56372d34ccbe34c3d3f3c577` with committed lockfile; PCR-selection/JSON CLI contract checked; wrapper tests request `0,2,4,7,11` and hardware-bound user data, reject incomplete evidence/fallback and do not claim remote verification |
| Complete Marketplace conversion | **Passed on 2026-11-12** using an unbooted disk from the exact Marketplace pin on a dedicated Ubuntu 24.04 Azure builder; complete build marker includes fixed-VHD validation |
| Kernel/driver compatibility | Matching signed-module gates passed for original kernel `7.0.0-1016-azure-fde`; source configuration includes `CONFIG_INTEL_TDX_GUEST=y`; this kernel booted the tested Intel TDX `Standard_DC4es_v6` CVM |
| Real live dm-verity corruption | **Passed in the complete image build**: disposable corrupted data rejected both offline verification and live mapper reads, with mapper status `C` |
| Visible volatile-overlay backing | **Passed on the builder and actual CVM**: bind-mounted read-only lower, retained tmpfs upper/work, required initrd guard and runtime guard passed, including after a real reboot |
| Azure signer enrollment / CVM boot | Gallery 1.0.3 replication and exact custom signer/Microsoft template readback passed; `VMGuestStateOnly` booted with Secure Boot enabled and the independently expected signer present in actual firmware db. Initial and corrected-image `NonPersistedTPM` attempts reported Compute `SecureBootClientError` |
| First boot / workload / real reboot | **Passed**: full verity verification and firmware/kernel/hash/device/agent checks on both boots; 136 MiB and 2,051 measured tmpfs inodes; distinct reboot ID and entire workload absent |
| Actual PCR-11 quote / MAA flow | Installed SDK failed on omitted systemd phase events; genuine combined replay matches actual PCR 11. Tmpfs-only supplemented-log diagnostic obtained a real custom-policy MAA token: signature/issuer/lifetime and signed raw values verified for `0-8,11`. PCR 12 was requested but omitted from token coverage. **Installed collector and independent expected-UKI verification remain unqualified** |

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
relocates the verified lower and retains the backing tmpfs at visible paths. The
real-image builder probe, both actual Dracut/signed-UKI component tests, and a
new complete Marketplace conversion passed with this change. The corrected
image passed signed-section, live-corruption and fixed-VHD gates; version 1.0.1
alone did not establish a successful CVM boot.

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
deallocated; stateless boot and its guest/reboot/MAA qualification remain blocked.

The corrected image was subsequently retried with upstream's persisted
`VMGuestStateOnly` mode. Its serial log proves Secure Boot enabled, the expected
embedded root hash and successful verity activation. It then exposed a second
setup issue: PID 1's shared parent mount forbids `mount --move` of `/sysroot`.
The helper now binds the read-only lower at its retained path, unmounts the
original lower and installs the overlay without globally changing mount
propagation. A real-image probe with an explicitly shared parent reproduced
the old move failure and passed the new helper and both root guards; all 15
policy tests and both real userspace component tests passed. The subsequent
complete image 1.0.3 build also passed signed-section, corruption and fixed-VHD
gates. A transient negative-mapper cleanup warning was followed by an explicit
check confirming that the owned negative mapper no longer existed. Azure
first/reboot acceptance subsequently passed for this change, as recorded below.

## Demonstrated persisted-TPM acceptance

Gallery version 1.0.3 booted on an Intel TDX `Standard_DC4es_v6` CVM in westus3,
using `VMGuestStateOnly`, Secure Boot and vTPM. The OS disk was an ordinary
64-GiB managed disk: no ephemeral `diffDiskSettings`, confidential OS-disk
encryption or `DiskWithVMGuestState` was requested.

The independent verifier confirmed the actual firmware signer, exact original
kernel and embedded command line, live data/hash devices and root hash, healthy
read-only verity lower, visible tmpfs backing, no swap and usable guest agent.
It fully verified the data partition on both boots. The first workload consumed
142,610,432 bytes (136 MiB) and 2,051 inodes. The boot ID changed from
`7cba07f7-01c5-42b7-b8af-c97a4927989b` to
`33486c1d-892b-46d9-8cbd-2c4986e1cc5a`, and the entire workload disappeared.
Independently retained private build and guest JSON records support these
results; raw VM-identifying evidence and tokens are not published here.

After reboot, cloud-init completed with exit 2 and an Azure provisioning-media
unavailable / IMDS fallback warning, but no fatal errors. The externally supplied
verifier now explicitly checks JSON status and fatal-error arrays, reports and
retains recoverable warnings, and rejects incomplete or failed provisioning.
The full post-reboot recheck passed without rebuilding the VHD.

## Shared-endpoint MAA investigation: no successful token

The installed pinned SDK submitted genuine CVM evidence to
`https://sharedwus3.wus3.attest.azure.net`. Its initial request quoted SHA-256
PCRs `0,2,4,7,11`; MAA returned HTTP 400 `PcrDoesNotMatchTcg` for PCR 11.
Evidence collection took approximately 128 ms; this was not a long MAA wait.
Full guest disk verification and Azure managed-command scheduling are separate
operations.

The SDK reads only `/sys/kernel/security/tpm0/binary_bios_measurements`.
Systemd additionally extends the actual `sysinit` and `ready` phase strings into
PCR 11 and records their digests in `/run/log/systemd/tpm2-measure.log`.
It does **not** measure journal contents. Read-only TPM PCR reads and replay of
genuine captured records established:

| SHA-256 PCR 11 source | Value |
| --- | --- |
| Firmware log replay only | `edf97efb412cf0d2b128a660dbc686091a5c280c7216ce06f476b2e45e5f7e86` |
| Firmware plus genuine phase events | `c8f073389ac35102792ea15ca251171d9150920a36977ac886adbfd7f2f0605e` |
| Actual TPM read / PCR reported in MAA mismatch | `c8f073389ac35102792ea15ca251171d9150920a36977ac886adbfd7f2f0605e` |

A diagnostic in tmpfs appended the genuine recorded phase digests as TCG2
`EV_IPL` events to unaltered firmware bytes. Every appended digest was checked
against its actual phase string. A private mount namespace supplied this
explicitly supplemented input to the unchanged SDK; the protected image, TPM
and quote generation were unchanged. This is **not** an untouched firmware log.

With PCRs `0,2,4,7,11`, the error advanced to `PcrQuoteHashDoesNotMatch`:
`Pcr index 1 not present in quote`. A second diagnostic quoted all boot PCRs
`0,1,2,3,4,5,6,7,11`, retaining PCR 11. MAA then returned
`PolicyEvaluationError` / `PolicyValidationFailure` with the explicit deny rule:

```text
[type=="x-ms-azurevm-dbvalidated", value==false] => deny();
```

A further real SDK test selected exactly `0,1,2,3,4,5,6,7,8,11,12`,
using a fresh relying-party challenge and the same genuine supplemented log.
It received the same HTTP 400 `PolicyValidationFailure` / `dbvalidated=false`
denial, with no PCR-log or missing-PCR error reported. Adding PCRs 8 and 12 did
not change the observed policy blocker; no token was issued.

This demonstrates a further provider-policy rejection, distinct from firmware
Secure Boot acceptance and the earlier event-log/quote errors. It does not
prove successful token issuance, a general custom-key attestation prohibition,
or an independently authenticated expected-UKI result. No provider policy was
changed, Secure Boot disabled, PCR 11 omitted or synthetic evidence substituted.
The installed collector still uses its original firmware-only input and exact
PCR set; these tmpfs diagnostics are not an integrated SDK fix.

## Custom-policy endpoint: real token issued

A dedicated AAD-trust provider in westus3 was created for this test. Its actual
default `AzureGuest` policy matched the shared provider's retrieved policy.
Only the `[type=="x-ms-azurevm-dbvalidated", value==false] => deny();` rule was
removed. Default-key, debugger, Secure Boot, signing and dbx checks were kept.
Authenticated policy management used API `2025-06-01`; exact policy readback
matched the submitted bytes, SHA-256
`e573d47b3db3f9ce237ee4fa61a74cbf14523b19d6c0d63b8687b9a441c73409`.

The unchanged SDK, in the same private tmpfs mount-namespace diagnostic, used a
fresh relying-party challenge, genuine supplemented event log and PCR selection
`0,1,2,3,4,5,6,7,8,11,12`. The provider issued a real token. Its RS256 signature
was verified against keys obtained over HTTPS from that provider's discovered
JWKS endpoint; expected issuer and current validity interval were checked.
The signed token reports:

- `secureboot=true`, `x-ms-azurevm-dbvalidated=false`;
- default-key, debugger, signing and dbx validation claims remain true;
- `x-ms-azurevm-attested-pcrs` covers `0,1,2,3,4,5,6,7,8,11`;
- `x-ms-azurevm-attested-pcr-values` contains raw base64 SHA-256 digests for
  those PCRs, including PCR 7 and PCR 11; all match the submitted request.

### Known gap: MAA does not return PCR 12 in the tested token

PCR 12 is present in the submitted PCR set and values, with an all-zero digest,
but is **absent from the signed coverage/value claims**. Do not claim complete
coverage of the requested set or infer that a zero PCR alone explains omission:
the token includes PCR 8 with a zero value.

Keep PCR 12 in the required expanded request `0-8,11,12`; do not remove it to
hide the gap. Until the provider returns both its signed coverage and value,
a relying party cannot verify PCR 12 from this token alone. The cause remains
unestablished; this observed gap is not a claim that every MAA configuration
omits PCR 12.

### Verified evidence and remaining limits

The signed PCR 11 value matches the independently replayed genuine measurements
above. The submitted event-log SHA-256 matches the diagnostic's exact normalized
bytes. Full SDK response and stderr were retrieved with independent SHA-256
checks and retained privately. No raw token or VM-identifying evidence is
published here. The custom endpoint and working Gallery image are explicitly
retained per the test owner's instruction.

This is successful custom-policy token issuance, not proof that the installed
collector works without supplementation. Removing default db validation does
not pin our custom signer or expected UKI. Independent AK/quote/TEE and challenge
binding and full expected-UKI identity checks are not implemented by this
diagnostic; no production-complete remote image verifier is claimed.

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
   `VMGuestStateOnly`; record the actual Azure image/security profile. This
   persists encrypted VMGS but does not request confidential OS-disk encryption.
   Qualify `NonPersistedTPM` separately rather than inheriting these results.
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
   calling the expected root hash attested. A PCR-11 coverage index is not its
   digest; the observed signed `x-ms-azurevm-attested-pcr-values` claim provides
   raw values only for the listed PCRs. User-data binds the TEE report, not
   quote `extraData`.

No production-complete root-hash attestation, rollback protection, immutable
effective-root executable policy, OS-disk encryption or general CVM-family
compatibility is claimed. Existing Ubuntu 22.04 or Azure Linux experiments do
not supply missing evidence for this independent Ubuntu 24.04 boot chain.
