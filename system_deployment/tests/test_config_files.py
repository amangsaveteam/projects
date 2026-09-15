import importlib.util
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
