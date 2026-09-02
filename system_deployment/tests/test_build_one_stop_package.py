#!/usr/bin/env python3
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_one_stop_package", ROOT / "one_stop/build_one_stop_package.py")
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class OneStopPackageTest(unittest.TestCase):
    def test_builds_and_lists_a_file_url_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            common = directory / "common.deb"
            run = directory / "upperlimb.run"
            common.write_bytes(b"common")
            run.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            version = directory / "version.json"
            urls = directory / "urls.json"
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0", "output_name": "navi_one_stop_installer-2.0.0"}), encoding="utf-8")
            urls.write_text(json.dumps({
                "schema_version": 1,
                "targets": {
                    "pico-jazzy": {
                        "os_id": "ubuntu", "os_version": "24.04", "architecture": "amd64",
                        "common": {
                            "url": common.as_uri(), "sha256": builder.file_sha256(common),
                            "configure_target": "pico-jazzy",
                            "configure_tool": "/usr/lib/navi-pico-common-dep/deploy_common.py",
                            "installer": "/usr/sbin/configure_pico_jazzy_environment.sh",
                        },
                        "extra_debs": [],
                        "runs": [{"name": "upperlimb", "url": run.as_uri(), "sha256": builder.file_sha256(run)}],
                    }
                },
            }), encoding="utf-8")

            output = builder.build(version, urls, directory / "out")
            info = subprocess.run([str(output), "--", "--info"], text=True, capture_output=True, check=True)
            verification = subprocess.run([str(output), "--", "--verify"], text=True, capture_output=True, check=True)

        self.assertIn("Version: 2.0.0", info.stdout)
        self.assertIn("pico-jazzy", info.stdout)
        self.assertIn("payloads/pico-jazzy/common.deb", verification.stdout)


if __name__ == "__main__":
    unittest.main()
