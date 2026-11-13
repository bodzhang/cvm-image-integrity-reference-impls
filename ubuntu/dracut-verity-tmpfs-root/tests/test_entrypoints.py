#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

RECIPE = Path(__file__).resolve().parents[1]


class ShellEntrypointTests(unittest.TestCase):
    def test_deployment_and_cleanup_forward_paths_and_propagate_failures(self):
        for script, action, arguments in (
            ("deploy.sh", "deploy", ["--run-directory", "/private/run with spaces", "--plan"]),
            ("cleanup.sh", "cleanup", ["--run-directory", "/private/run with spaces", "--remove-run"]),
        ):
            for status in (0, 17):
                with self.subTest(script=script, status=status), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    python = root / "python3"
                    python.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FORWARDED"\n'
                                      'exit "$PYTHON_STATUS"\n')
                    python.chmod(0o755)
                    forwarded = root / "forwarded"
                    env = dict(os.environ, PATH=str(root) + ":" + os.environ["PATH"],
                               FORWARDED=str(forwarded), PYTHON_STATUS=str(status))
                    result = subprocess.run(["bash", str(RECIPE / script), *arguments],
                                            cwd=directory, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, status, result.stderr)
                    self.assertEqual(forwarded.read_text().splitlines(),
                                     [str(RECIPE / "scripts/deploy.py"), action, *arguments])


if __name__ == "__main__":
    unittest.main()
