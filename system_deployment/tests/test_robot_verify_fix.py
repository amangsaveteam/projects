import importlib.util
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "robot_verify_fix", ROOT / "one_stop/install_robot_with_verify_fix.py"
)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RobotVerifyFixTest(unittest.TestCase):
    def script(self, service_status=3, check_status=0):
        return '''set -euo pipefail
LEGACY_ROBOT_SERVICES="old-a.service old-b.service"
systemctl() { return %d; }
check_package() { return %d; }
die() { echo "ERROR: $*" >&2; exit 1; }
verify_robot_runtime() {
  check_package
  local legacy
%s
verify_robot_runtime
echo completed
''' % (service_status, check_status, helper.LEGACY_TAIL)

    def run_script(self, script):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_inactive_services_succeed_after_fix(self):
        original = self.script()
        self.assertEqual(self.run_script(original).returncode, 3)
        result = self.run_script(helper.fix_robot_verifier(original))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("completed", result.stdout)

    def test_active_service_still_fails(self):
        result = self.run_script(helper.fix_robot_verifier(self.script(service_status=0)))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Legacy service is still active", result.stderr)
        self.assertNotIn("completed", result.stdout)

    def test_earlier_failure_is_preserved(self):
        result = self.run_script(helper.fix_robot_verifier(self.script(check_status=7)))
        self.assertEqual(result.returncode, 7)
        self.assertNotIn("completed", result.stdout)

    def test_already_fixed_is_unchanged(self):
        fixed = helper.fix_robot_verifier(self.script())
        self.assertEqual(helper.fix_robot_verifier(fixed), fixed)

    def test_unrecognized_script_is_rejected(self):
        with self.assertRaises(RuntimeError):
            helper.fix_robot_verifier("echo unknown\n")
        with self.assertRaises(RuntimeError):
            helper.fix_robot_verifier(self.script().replace("  done", "  done # changed"))


if __name__ == "__main__":
    unittest.main()
