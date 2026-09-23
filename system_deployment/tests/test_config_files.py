import importlib.util
import os
import pwd
import grp
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("config_builder", Path(__file__).resolve().parents[1] / "one_stop/build_one_stop_package.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ConfigFilesTest(unittest.TestCase):
    def test_stages_nested_content_and_checksums_and_rejects_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "config/nested"
            source.mkdir(parents=True)
            (source / "test.yaml").write_text("value: 1\n")
            target = {"config_files": [
                {"source": "config", "destination": "/home/naviai/navi_project/config", "owner": "naviai"},
                {"source": "config", "destination": "/etc/naviai/environment.d", "overwrite": False},
            ]}
            checksums = []
            with patch.object(builder, "DEPLOYMENT_ROOT", root / "system_deployment"):
                builder.stage_config_files(root / "stage", "orin-humble", target, checksums, False)
                payload = root / "stage" / checksums[0][1]
                self.assertEqual(payload.read_text(), "value: 1\n")
                self.assertEqual(checksums[0][0], builder.file_sha256(payload))
                script = root / "stage/targets/orin-humble/install-config-files.sh"
                subprocess.run(["bash", "-n", str(script)], check=True)
                rendered = script.read_text()
                self.assertIn("cp --backup=numbered", rendered)
                self.assertIn("Keeping local configuration", rendered)
                (source / "link").symlink_to(source / "test.yaml")
                with self.assertRaises(builder.BuildError):
                    builder.stage_config_files(root / "stage", "orin-humble", target, [], True)


    def test_model_filtered_config_runs_in_child_shell(self):
        for robot_type, expected in (("WA-T", True), ("JK2-V1", True), ("WA", False)):
            with self.subTest(robot_type=robot_type), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "config"
                source.mkdir()
                (source / "test.yaml").write_text("value: 1\n")
                destination = root / "deployed/config"
                target = {"config_files": [{
                    "source": "config", "destination": str(destination),
                    "owner": pwd.getpwuid(os.getuid()).pw_name,
                    "group": grp.getgrgid(os.getgid()).gr_name,
                    "robot_types": ["WA-T", "JK2-V1"],
                }]}
                with patch.object(builder, "DEPLOYMENT_ROOT", root / "system_deployment"):
                    builder.stage_config_files(root / "stage", "orin-humble", target, [], False)
                script = root / "stage/targets/orin-humble/install-config-files.sh"
                result = subprocess.run(["bash", str(script), robot_type], capture_output=True, text=True,
                                        env={k: v for k, v in os.environ.items() if k != "robot_type"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((destination / "test.yaml").exists(), expected)
                missing = subprocess.run(["bash", str(script)], capture_output=True, text=True)
                self.assertNotEqual(missing.returncode, 0)
                self.assertIn("robot type is required", missing.stderr)

    def test_config_child_receives_model_from_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target_dir = root / "targets/orin-humble"
            target_dir.mkdir(parents=True)
            (target_dir / "install-config-files.sh").write_text('set -eu\nprintf "%s" "$1"\n')
            script = builder.target_install("orin-humble", "config", "", None, [], [], [])
            call = next(line for line in script.splitlines() if "then bash" in line and "install-config-files.sh" in line)
            result = subprocess.run(["bash", "-c", 'set -eu; root="$1"; robot_type=WA-T; ' + call,
                                     "test", str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "WA-T")
