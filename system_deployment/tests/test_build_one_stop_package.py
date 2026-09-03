#!/usr/bin/env python3
import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_one_stop_package", ROOT / "one_stop/build_one_stop_package.py")
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class OneStopPackageTest(unittest.TestCase):
    def test_extra_installer_environment_is_scoped_to_that_installer(self) -> None:
        script = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [("payloads/orin-humble/extra-02.deb", ["/usr/lib/orin-robot-common-deb/install_robot_deps.sh"], ["PIP_NO_BUILD_ISOLATION=1"])],
            [], [],
        )

        self.assertIn(
            'env PIP_NO_BUILD_ISOLATION=1 "/usr/lib/orin-robot-common-deb/install_robot_deps.sh"',
            script,
        )
        self.assertEqual(builder.resolve_environment({"PIP_NO_BUILD_ISOLATION": "1"}, "test"), ["PIP_NO_BUILD_ISOLATION=1"])
        with self.assertRaises(builder.BuildError):
            builder.resolve_environment({"invalid-name": "1"}, "test")

    def test_system_config_replaces_common_deb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            version = directory / "version.json"
            urls = directory / "urls.json"
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0", "output_name": "navi_one_stop_installer-2.0.0"}), encoding="utf-8")
            urls.write_text(json.dumps({
                "schema_version": 1,
                "targets": {
                    "pico-jazzy": {
                        "os_id": "ubuntu", "os_version": "24.04", "architecture": "amd64",
                        "system_config": {"configure_target": "pico-jazzy", "base_image_contract": "pico-jazzy"},
                        "extra_debs": [], "runs": [],
                    }
                },
            }), encoding="utf-8")

            output = builder.build(version, urls, directory / "out")
            with tarfile.open(fileobj=io.BytesIO(output.read_bytes().split(b"\n", 9)[9]), mode="r:gz") as archive:
                names = archive.getnames()
                install = archive.extractfile("targets/pico-jazzy/install.sh").read().decode("utf-8")
                pretest = archive.extractfile("targets/pico-jazzy/pretest.sh").read().decode("utf-8")
                system_install = archive.extractfile("targets/pico-jazzy/install-system-config.sh").read().decode("utf-8")
                master_install = archive.extractfile("install.sh").read().decode("utf-8")
                target_manifest = archive.extractfile("targets/pico-jazzy/payloads.sha256").read().decode("utf-8")

        self.assertIn("payloads/pico-jazzy/system-config/deploy_common.py", names)
        self.assertIn("payloads/pico-jazzy/system-config/configs/robot-types.json", names)
        self.assertNotIn("payloads/pico-jazzy/common.deb", names)
        self.assertIn("install-system-config.sh", install)
        self.assertIn('sha256sum -c "targets/pico-jazzy/payloads.sha256"', install)
        self.assertIn("payloads/pico-jazzy/system-config/deploy_common.py", target_manifest)
        self.assertNotIn("payloads/orin-humble/", target_manifest)
        self.assertIn("System configuration: deploy/update", pretest)
        self.assertIn("configured=$(sed -n 's/^ROBOT_TYPE=//p' /etc/zj_humanoid/device.env", master_install)
        self.assertIn("bare device requires --robot-type TYPE", master_install)
        self.assertIn("Robot type: not configured", master_install)
        self.assertNotIn("managed_services=(", install)
        subprocess.run(["bash", "-n"], input=install + pretest + system_install + master_install, text=True, check=True)

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

    def test_managed_services_are_stopped_until_total_install_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            version = directory / "version.json"
            urls = directory / "urls.json"
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0", "output_name": "navi_one_stop_installer-2.0.0"}), encoding="utf-8")
            urls.write_text(json.dumps({
                "schema_version": 1,
                "targets": {
                    "pico-jazzy": {
                        "os_id": "ubuntu", "os_version": "24.04", "architecture": "amd64",
                        "system_config": {"configure_target": "pico-jazzy", "base_image_contract": "pico-jazzy"},
                        "managed_services": ["navi-pico-upperlimb.service"],
                        "extra_debs": [], "runs": [],
                    }
                },
            }), encoding="utf-8")

            output = builder.build(version, urls, directory / "out")
            with tarfile.open(fileobj=io.BytesIO(output.read_bytes().split(b"\n", 9)[9]), mode="r:gz") as archive:
                install = archive.extractfile("targets/pico-jazzy/install.sh").read().decode("utf-8")

        self.assertIn('managed_services=("navi-pico-upperlimb.service")', install)
        self.assertLess(install.index("systemctl stop \"$unit\""), install.index("install-system-config.sh"))
        self.assertLess(install.index("install-system-config.sh"), install.index("systemctl restart \"$unit\""))
        self.assertIn("managed services are being kept stopped", install)


if __name__ == "__main__":
    unittest.main()
