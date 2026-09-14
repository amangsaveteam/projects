import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "checker", Path(__file__).resolve().parents[1] / "check_installation.py")
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class InstallationCheckTest(unittest.TestCase):
    def test_failure_does_not_stop_later_checks(self):
        checks = checker.Checks(1)
        checks.check("missing", lambda: (_ for _ in ()).throw(FileNotFoundError("missing")))
        checks.check("next", lambda: "ok")
        self.assertEqual([r["state"] for r in checks.rows], ["FAIL", "PASS"])

    def test_command_failure_and_timeout_are_reported(self):
        checks = checker.Checks(1)
        with patch.object(checker.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["systemctl"], 3, "inactive", "")):
            checks.check("service", lambda: checks.command(["systemctl", "is-active", "example"]))
        with patch.object(checker.subprocess, "run", side_effect=subprocess.TimeoutExpired("systemctl", 1)):
            checks.check("timeout", lambda: checks.command(["systemctl", "is-active", "example"]))
        self.assertTrue(all(r["state"] == "FAIL" for r in checks.rows))

    def test_target_contract_distinguishes_humble_and_jazzy(self):
        self.assertIn("robot", checker.MODULES["orin-humble"])
        self.assertEqual(set(checker.MODULES["orin-jazzy"]), {"vision"})
        self.assertFalse(checker.MODULES["pico-humble"]["robot"][2])
