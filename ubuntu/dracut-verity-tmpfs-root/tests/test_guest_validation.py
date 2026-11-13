#!/usr/bin/env python3
"""Cloud-init acceptance must distinguish reported warnings from fatal errors."""

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "verify_guest", Path(__file__).resolve().parents[1] / "verify-guest.py")
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class CloudInitTests(unittest.TestCase):
    def check(self, code=0, **changes):
        status = {"status": "done", "errors": [], "recoverable_errors": {}}
        status.update(changes)
        result = subprocess.CompletedProcess([], code, json.dumps(status), "")
        output = io.StringIO()
        with patch.object(guest.subprocess, "run", return_value=result), contextlib.redirect_stderr(output):
            accepted = guest.cloud_init_status()
        return accepted, output.getvalue()

    def test_accepts_completed_clean_status(self):
        status, output = self.check()
        self.assertEqual(status["status"], "done")
        self.assertEqual(output, "")

    def test_accepts_and_reports_recoverable_imds_warning(self):
        warnings = {"WARNING": ["Provisioning media unavailable; using IMDS"]}
        status, output = self.check(2, recoverable_errors=warnings)
        self.assertEqual(status["recoverable_errors"], warnings)
        self.assertIn("CVM_CLOUD_INIT_RECOVERABLE_WARNINGS", output)
        self.assertIn("using IMDS", output)

    def test_rejects_fatal_incomplete_and_malformed_status(self):
        for code, changes in (
            (1, {}), (3, {}), (0, {"status": "running"}),
            (2, {"errors": ["fatal"]}),
            (2, {"init": {"errors": ["fatal stage"]}}),
            (0, {"recoverable_errors": None}),
            (0, {"errors": None}),
        ):
            with self.subTest(code=code, changes=changes), self.assertRaises(RuntimeError):
                self.check(code, **changes)


class CollectorTests(unittest.TestCase):
    def test_checks_installed_collector_against_independent_build_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "usr/local/bin/azure-guest-attest"
            wrapper = root / "usr/local/bin/cvm-attest"
            record = root / "etc/cvm-verity/attestation-sdk.json"
            binary.parent.mkdir(parents=True)
            record.parent.mkdir(parents=True)
            binary.write_bytes(b"native collector fixture")
            wrapper.write_bytes(b"expanded PCR wrapper fixture")
            sdk = {"revision": "fixture", "patch": {"sha256": "patch fixture"},
                   "binarySha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
            record.write_text(json.dumps(sdk))
            expected = {"attestationSdk": dict(
                sdk, wrapperSha256=hashlib.sha256(wrapper.read_bytes()).hexdigest())}
            self.assertEqual(guest.verify_attestation_collector(expected, root),
                             expected["attestationSdk"])
            for target in (binary, wrapper, record):
                original = target.read_bytes()
                target.write_bytes(b"{}" if target == record else b"old collector or changed wrapper")
                with self.subTest(target=target), self.assertRaises(RuntimeError):
                    guest.verify_attestation_collector(expected, root)
                target.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
