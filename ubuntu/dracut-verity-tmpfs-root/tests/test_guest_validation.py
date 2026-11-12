#!/usr/bin/env python3
"""Cloud-init acceptance must distinguish reported warnings from fatal errors."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
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


if __name__ == "__main__":
    unittest.main()
