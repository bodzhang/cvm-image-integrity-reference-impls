#!/usr/bin/env python3
import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

RECIPE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("attest_guest", RECIPE / "attest-guest.py")
attest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attest)
ENDPOINT = "https://example.attest.azure.net"
NONCE = "ab" * 32


def b64(value):
    return base64.b64encode(value).decode()


def response_fixture():
    tpm = {
        "PcrSet": list(attest.PCRS),
        "PCRs": [{"Index": index, "Digest": b64(bytes([index + 1]) * 32)} for index in attest.PCRS],
        "PcrQuote": b64(b"quote-fixture-not-cryptographically-verified"),
        "PcrSignature": b64(b"signature-fixture"),
        "AikCert": b64(b"certificate-fixture"),
        "AikPub": b64(b"key-fixture"),
    }
    request = {
        "TpmInfo": tpm, "TcgLogs": b64(b"event-log-fixture"),
        "IsolationInfo": {"Type": "SevSnp", "Evidence": {
            "Proof": b64(b"TEE-proof-fixture"), "RunTimeData": b64(b'{"user-data":"fixture"}'),
        }},
    }
    return {
        "provider": "maa", "endpoint": ENDPOINT, "passed": True,
        "token": "unverified-token-fixture", "request": json.dumps(request),
    }


class AttestationTests(unittest.TestCase):
    def test_requests_exact_pcr_set_including_uki_pcr11_and_hardware_user_data(self):
        response = response_fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "sdk.json"
            config.write_text('{"revision":"fixture"}')
            with patch.object(attest.os, "geteuid", return_value=0), \
                    patch.object(attest, "SDK_BUILD", config), \
                    patch.object(attest.subprocess, "run", return_value=SimpleNamespace(
                        returncode=0, stdout=json.dumps(response))) as command:
                receipt = attest.collect(ENDPOINT, NONCE, root / "output")
            arguments = command.call_args.args[0]
            self.assertIn("guest-attest", arguments)
            self.assertEqual(arguments[arguments.index("--pcr-index") + 1], "0,2,4,7,11")
            self.assertEqual(arguments[arguments.index("--user-data") + 1], "hex:" + NONCE)
            self.assertNotIn("--client-payload", arguments)
            self.assertIn("--show-request", arguments)
            self.assertEqual(receipt["result"], "EVIDENCE_COLLECTED")
            self.assertTrue(receipt["verification"].startswith("NOT_VERIFIED"))
            self.assertEqual(receipt["reportedPcr11Sha256"], "0c" * 32)
            self.assertEqual(json.loads((root / "output/request.json").read_text())["TpmInfo"]["PcrSet"],
                             [0, 2, 4, 7, 11])

    def test_rejects_loopback_wrong_endpoint_and_token_absence(self):
        for change in ({"provider": "loopback"}, {"endpoint": "https://other.attest.azure.net"},
                       {"passed": False}, {"token": ""}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                attest.validate_response(dict(response_fixture(), **change), ENDPOINT)

    def test_rejects_missing_uki_measurement_or_tee_evidence(self):
        response = response_fixture()
        original = json.loads(response["request"])
        variants = []
        request = copy.deepcopy(original)
        request["IsolationInfo"]["Type"] = "TrustedLaunch"
        variants.append(request)
        request = copy.deepcopy(original)
        request["TpmInfo"]["PcrSet"].remove(11)
        variants.append(request)
        request = copy.deepcopy(original)
        request["TpmInfo"]["PCRs"][-1]["Digest"] = b64(bytes(32))
        variants.append(request)
        request = copy.deepcopy(original)
        request["TpmInfo"]["PCRs"][-1]["Digest"] = b64(b"short")
        variants.append(request)
        request = copy.deepcopy(original)
        request["TpmInfo"]["PCRs"].append(request["TpmInfo"]["PCRs"][-1])
        variants.append(request)
        for field in ("PcrQuote", "PcrSignature", "AikCert", "AikPub"):
            request = copy.deepcopy(original)
            request["TpmInfo"][field] = ""
            variants.append(request)
        request = copy.deepcopy(original)
        request["TcgLogs"] = ""
        variants.append(request)
        for request in variants:
            with self.subTest(request=request), self.assertRaises(ValueError):
                attest.validate_response(dict(response, request=json.dumps(request)), ENDPOINT)

    def test_sdk_failure_retains_diagnostics_but_no_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with patch.object(attest.os, "geteuid", return_value=0), \
                    patch.object(attest.subprocess, "run", return_value=SimpleNamespace(
                        returncode=2, stdout="no TEE report")), self.assertRaisesRegex(ValueError, "SDK failed"):
                attest.collect(ENDPOINT, NONCE, output)
            self.assertFalse((output / "receipt.json").exists())
            self.assertTrue((output / "sdk-stderr.log").exists())

    def test_rejects_unbound_or_invalid_challenge_and_non_maa_endpoint(self):
        for endpoint, nonce in (("http://example.attest.azure.net", NONCE),
                                ("https://example.attest.azure.net/attest/SevSnpVm", NONCE),
                                ("https://user:password@example.attest.azure.net", NONCE),
                                (ENDPOINT, ""), (ENDPOINT, "ab" * 31), (ENDPOINT, "AB" * 32)):
            with self.subTest(endpoint=endpoint, nonce=nonce), self.assertRaises(ValueError):
                attest.validate_options(endpoint, nonce)

    def test_sdk_revision_and_pcr_policy_are_pinned(self):
        sdk = json.loads((RECIPE / "inputs.json").read_text())["attestationSdk"]
        self.assertRegex(sdk["revision"], r"^[0-9a-f]{40}$")
        self.assertEqual(sdk["pcrs"], list(attest.PCRS))


if __name__ == "__main__":
    unittest.main()
