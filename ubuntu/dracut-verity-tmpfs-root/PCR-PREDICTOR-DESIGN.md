# Offline PCR4, PCR7 and PCR11 predictor design

[Recipe](README.md) | [Invocation and input preparation](LOCAL-BUILD.md#optional-offline-pcr-prediction) | [Qualification evidence](VALIDATION.md#evidence-levels)

## Status and purpose

[predict-pcrs.py](scripts/predict-pcrs.py) calculates expected SHA-256 PCR4,
PCR7 and PCR11 values **without booting the candidate image or reading its
event log**. It combines final build artifacts with an independently approved
firmware baseline and an explicit boot-phase path. It neither changes the
image nor contacts Azure, a TPM or Microsoft Azure Attestation (MAA).

The implemented profile, `azure-direct-uki-v1`, models direct firmware launch
of one signed x64 systemd-stub 255 UKI. Artifact-based predictions matched
retained MAA-signed values for the tested reference. **Obtaining an independently
approved base Secure Boot database remains unresolved.** The qualification
baseline came from a separate controlled reference, not a published or approved
Microsoft factory baseline. Matching those observations validates the model;
it does not establish production trust in that baseline.

The current baseline and boot profile describe a **specific observed Azure
implementation**, not a stable Azure API contract or a universal Azure default.
No publicly available, approved exact-byte baseline has been identified for
this profile. Relevant Azure implementation changes require requalification:
changed variable contents require a new independently approved baseline, while
changed enrollment serialization, authority selection or measurement sequences
may also require changes to the predictor model. The same UKI can therefore
produce different PCRs after a platform change.

This is a predictor, not a complete remote-attestation verifier or a general
UEFI event-log simulator. Default builds/controllers do not invoke it.

## Inputs, outputs and trust boundaries

```text
Independently retained build             Independently controlled policy
  final signed cvm.efi                     firmware variable baseline + SHA256 pin
  exact public signer.der                 enrollment owner GUID + phase path
                  \                       /
                   -> validate and predict
                   -> expected-pcrs.json
                              |
                   approve/version expected policy
                              |
                   future relying-party verifier
                   compares authenticated, fresh evidence
```

| Input | Meaning and trust requirement |
| --- | --- |
| `--uki` | Final signed UKI from the approved build. A guest-retrieved copy must first match independently retained build hashes. |
| `--signer-der` | Exactly one canonical DER certificate. OpenSSL checks its encoding and `sbverify` verifies the UKI against it; this does not prove firmware enrollment or revocation-policy acceptance. No private key is needed. |
| `--firmware-baseline` | Schema-version-1 JSON containing `bootProfile`, `signatureOwnerGuid`, `variables` and nonempty `provenance`. Variables are base64-encoded complete values for `SecureBoot`, `PK`, `KEK`, `db` and `dbx`. |
| `--firmware-baseline-sha256` | Lowercase SHA-256 of the exact JSON file bytes, obtained from independently approved policy. Reformatting the file changes the pin. Hashing an untrusted file at invocation time does not approve it. |
| `--phase-path` | Explicit colon-separated ordered PCR11 extensions. `sysinit:ready` matched the retained reference; an empty string requests UKI-only, not boot-complete state. |

`signatureOwnerGuid` describes the **new signer enrollment**, while `variables`
describe the baseline **before that signer is appended**. The baseline `db`
must not already contain the supplied certificate. `SecureBoot` must be the
single byte `01`; all other variables must be nonempty EFI signature-list
streams. The parser accepts only X509 and SHA256 list types with zero-length
signature headers. It checks structure, not the trustworthiness of every
baseline certificate. It rejects duplicate/unexpected JSON fields, unsupported
profiles and a baseline file larger than 256 KiB.

The output includes final `pcrs` with string keys `"4"`, `"7"` and `"11"`,
ordered event digests and intermediate PCR values, cumulative
`pcr11Checkpoints`, model identifiers and the SHA-256 hashes of all three input
files. No prediction JSON is emitted on handled input/signature/dependency
failure; the CLI exits with status 2. Output is not signed or approved by the
tool: the relying party must protect, review and version it as expected policy.

## Measurement model

All three PCRs start at 32 zero bytes for the modeled boot. Each event extends:

```text
PCR_next = SHA256(PCR_current || event_digest)
```

Event order and exact serialized bytes matter; matching a set of certificate
fingerprints or section hashes is insufficient.

### PCR4: launched EFI application

The profile extends exactly three digests:

1. SHA256 of ASCII `Calling EFI Application from Boot Option`, without a NUL.
2. SHA256 of four zero bytes (`EV_SEPARATOR`).
3. The final UKI's PE Authenticode SHA256 digest, not its whole-file SHA256.

Authenticode excludes the PE checksum, security-directory entry and certificate
table. It includes raw section padding and any noncertificate overlay. The
parser requires an x64 PE32+ EFI application, contiguous nonoverlapping raw
sections, nonoverlapping virtual sections and exactly one end-of-file PKCS7
signature. These constraints intentionally narrow the supported hash model.

There is no shim, GRUB, other EFI application or separately measured EFI kernel
launch in this sequence. A different boot path needs a new qualified model;
the predictor cannot detect the target's actual boot path from a UKI alone.

### PCR7: Secure Boot variables and authorizing certificate

Let `C` be the exact signer DER bytes and `O` the enrollment owner GUID in
UEFI little-endian byte representation. The predictor constructs one new
`EFI_SIGNATURE_LIST` and appends it to the complete baseline `db`:

```text
SignatureType       = EFI_CERT_X509_GUID
SignatureListSize   = 44 + len(C)
SignatureHeaderSize = 0
SignatureSize       = 16 + len(C)
entry               = O || C
effective_db        = baseline_db || new_signature_list
```

It extends, in order, `SecureBoot`, `PK`, `KEK`, effective `db`, `dbx`, the
four-zero-byte separator, and exactly one `db` authority event.
Each variable digest hashes this serialized payload:

```text
vendor GUID (16 bytes, little-endian)
|| UnicodeNameLength (uint64 little-endian, UTF16 code-unit count)
|| VariableDataLength (uint64 little-endian, byte count)
|| variable name (UTF16-LE, no terminating NUL)
|| variable value
```

`SecureBoot`, `PK` and `KEK` use `EFI_GLOBAL_VARIABLE` GUID
`8be4df61-93ca-11d2-aa0d-00e098032b8c`; `db` and `dbx` use
`EFI_IMAGE_SECURITY_DATABASE_GUID` `d719b2cb-3d3a-4596-a3bc-dad00e67656f`.
The authority uses the same `db` wrapper, but its value is only `O || C`,
not the complete signature list or a public-key hash.

Consequently, a stable default `db` alone is not enough. PK/KEK/dbx contents,
certificate DER encoding, signature-list boundaries/order, owner GUID and the
authority/event sequence must also match. Firmware selecting another permitted
authority or adding another authority event changes PCR7.

### PCR11: UKI content and explicitly selected phases

For each present, nonempty section in the following **model order**, the
predictor extends SHA256 of the ASCII section name including its terminating
NUL, then SHA256 of the section's `VirtualSize` bytes:

```text
.linux .osrel .cmdline .initrd .splash .dtb .uname .sbat .pcrpkey
```

Raw file padding is excluded; measured section data extending beyond its raw
storage is rejected. `.pcrsig` is deliberately not measured into PCR11;
`.pcrpkey`, when present, is measured. A systemd-stub 255 `.sdmagic` marker is
required, but that marker is not independent proof of the stub implementation:
the UKI must still come from the approved build.

For each requested phase, extend SHA256 of its ASCII name without a NUL.
The output records the UKI-only checkpoint and every successive phase prefix.
The predictor does not inspect service enablement, discover phases, mask
services or prove a phase has occurred on the evaluated guest.

## Base database acquisition: the main open trust input

**No production-approved base `db`, acquisition helper or authoritative Azure
template-to-variable-bytes mapping is shipped here.** The Gallery publication
code requests `MicrosoftUefiCertificateAuthorityTemplate` plus the public signer
and checks exact configuration readback. That API configuration identifies the
requested template/certificate, not all serialized firmware variable bytes.
In particular, the controller does not specify the signature owner GUID in its
additional-signature request; the predictor currently requires that GUID as
an externally established input.

The base `db` means the complete firmware database value before custom signer
enrollment, not a list of downloaded Microsoft certificates. Rebuilding an
apparently equivalent database can change signature owners, list grouping or
ordering, and hence PCR7. The same byte-preservation requirement applies to
PK, KEK and dbx.

Identifying an internal template source does not establish a publicly available,
approved baseline or prove which template revision applies to a deployment.
The current reference-derived bytes and configuration assumptions must remain
scoped to the implementation that was observed; they must not be treated as
permanent defaults.

Two possible acquisition routes need qualification:

| Route | Required evidence | Current gap |
| --- | --- | --- |
| Platform-supplied baseline | An authenticated, versioned provider artifact specifying exact variable bytes, template/firmware applicability and signer-enrollment serialization/owner behavior. | No such artifact or retrieval procedure has been established by this implementation. Do not infer one from the template name or Gallery readback. |
| Organization-approved reference enrollment | Independently authorized reference provisioning with the intended template, controlled firmware state, complete raw-variable capture, measured-event comparison and separate baseline review. | This would be an explicitly governed trust bootstrap, not proof of Microsoft factory defaults. Acquisition, reference authentication and approval are not automated here. |

A proposed controlled-reference workflow is:

1. Define the supported platform scope: firmware/template revision where
   identifiable, VM security type and guest-state persistence mode, plus the
   boot path. Establish how reference identity and capture integrity will be
   authenticated independently of the workload under evaluation.
2. Prefer a reference provisioned with the intended default template and no
   custom signer. Capture all five complete variable values and compare them
   with the values actually measured at boot. Runtime reads alone may describe
   post-measurement state; capture timing and updates must be understood.
3. Qualify custom enrollment separately: establish the appended list's exact
   bytes, owner GUID, placement and the authority selected during direct UKI
   boot. Check that the other variables and event order remain unchanged.
4. Preserve source evidence and capture hashes, template/configuration
   readback, known firmware identifiers, acquisition method and limitations.
   Have the baseline independently reviewed; publish its exact file hash
   through authenticated policy configuration.
5. Test across the intended deployment scope and after relevant platform
   changes. If exact applicability cannot be established, leave the profile
   unsupported rather than broadening acceptance to whatever the target reports.

An approved reference may use firmware events to help characterize the model;
the evaluated target's own event log must never define its acceptance baseline.
Capturing a current variable value requires only the raw value bytes: strip
the four-byte attributes prefix **only for efivarfs input**, and exclude event
GUID/name/length wrappers. Preserve the remaining bytes unchanged.
See the [existing preparation example](LOCAL-BUILD.md#prepare-and-approve-the-firmware-baseline)
for packaging already obtained and approved values; it does not solve acquisition.

For the existing qualification, the base was reconstructed from a separate
retained reference by removing its exact known appended signer list, then
appending the candidate's independently retained signer. Cross-prediction
matched, including ordered event digests. This is useful experimental evidence,
but subtraction does not authenticate the remainder. Do not promote that
reconstruction, an arbitrary guest export or a freshly computed pin into
production policy without the independent approval process above.

## Remaining gaps and production gates

| Gap | Required closure |
| --- | --- |
| Baseline origin and enrollment determinism | Establish one of the acquisition routes above, including the complete variable bytes, owner GUID and authority-selection behavior. A provenance string and hash pin only record an approval decision; they cannot create trust. |
| Baseline lifecycle | Define authenticated distribution, applicability, revocation and rollback policy for firmware/template/PK/KEK/db/dbx changes. When Azure changes the applicable variable bytes, obtain, independently approve and pin a replacement baseline, then regenerate expected PCRs. Persisted guest firmware state can differ from newly provisioned defaults; qualify both initial and subsequent boots. Never auto-approve mismatch-derived baselines. |
| Profile and phase qualification | Requalify changed firmware, enrollment serialization, authority selection, measurement sequences, boot paths, stub implementations or phase services. Updating baseline bytes alone cannot accommodate changed measurement behavior; update and qualify the predictor model/profile where necessary. The current tool neither detects Azure implementation changes nor proves all systemd 255 variants behave identically. |
| Firmware acceptance | `sbverify` is not a full firmware trust/revocation emulator. Validate actual enrollment and boot behavior; a successful prediction does not guarantee firmware will accept the UKI. |
| Build integration and policy release | Decide where to invoke the predictor after final signing, retain its inputs/output, and approve/version expected values with each image release. Current invocation is manual and opt-in. |
| Relying-party verification | Implement authenticated quote/MAA claim validation, fresh challenge binding, required PCR coverage and comparison to independently approved predictions, with explicit mismatch rejection and rollback protection. Prediction JSON alone performs none of these. |
| PCR12 and external inputs | Addons and external credentials remain separate policy inputs. This tool neither predicts PCR12 nor disables those capabilities; tested MAA tokens omit PCR12. Matching PCR4/7/11 does not resolve that gap. |
| Runtime integrity | PCR matching does not establish that the writable tmpfs overlay or retained cloud-init/agent/SSH management paths are hardened. Apply the recipe's separate production-hardening policy. |

The [focused tests](tests/test_pcr_prediction.py) cover serialized-variable
vectors and input sensitivity, Authenticode exclusions/padding/overlay,
section/phase behavior, an independent `systemd-measure` oracle, malformed
inputs and CLI signature-failure behavior. The [validation record](VALIDATION.md#evidence-levels)
distinguishes those offline checks from retained live-reference evidence.
Neither test category closes the baseline approval or full verifier gaps.
