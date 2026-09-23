#!/usr/bin/env python3
"""Unit tests for the offline Common DEB carrier builder."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_offline_common_bundle", ROOT / "common/build_offline_common_bundle.py"
)
common_builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(common_builder)


class OfflineCommonBundleTest(unittest.TestCase):
    def test_dedicated_build_interfaces_select_their_single_target(self) -> None:
        expected = {
            "build_orin_humble_common_deb.sh": "orin-common-humble",
            "build_pico_humble_common_deb.sh": "pico-common",
        }
        for filename, config in expected.items():
            wrapper = ROOT / "common" / filename
            self.assertTrue(wrapper.is_file())
            self.assertTrue(wrapper.stat().st_mode & 0o111)
            self.assertIn(f"--config {config}", wrapper.read_text(encoding="utf-8"))

    def test_common_configs_carry_only_offline_dependencies(self) -> None:
        environment_paths = {
            "etc/profile.d/zj_humanoid.sh",
            "etc/zj_humanoid/cyclonedds.xml",
            "etc/zj_humanoid/device.env",
        }
        for config_path in (ROOT / "common/configs").glob("*-common.json"):
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["extra_files"], [], config_path.name)
            self.assertNotIn("environment_only", config, config_path.name)
            self.assertFalse(
                environment_paths & {item["destination"] for item in config["extra_files"]},
                config_path.name,
            )


    def test_compatibility_deb_installs_the_legacy_robot_package_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            artifacts = common_builder.build_compatibility_debs(
                {
                    "release_version": "2.0.0~humble+23",
                    "compatibility_debs": [{
                        "package_name": "orin-common-deb",
                        "artifact_filename": "orin_common_deb.deb",
                        "depends": ["navi-common-dep (= 2.0.0~humble+23)"],
                        "description": "legacy compatibility identity",
                    }],
                },
                output,
                {"architecture": "arm64"},
            )
            self.assertEqual(artifacts, [output / "orin_common_deb.deb"])
            self.assertEqual(common_builder.deb_field(artifacts[0], "Package"), "orin-common-deb")
            self.assertEqual(
                common_builder.deb_field(artifacts[0], "Depends"),
                "navi-common-dep (= 2.0.0~humble+23)",
            )

    def test_orin_humble_common_replaces_the_legacy_verifier_owner(self) -> None:
        config = json.loads((ROOT / "common/configs/orin-common-humble.json").read_text(encoding="utf-8"))
        self.assertEqual(config["deb_replaces"], ["orin-common-deb"])
        self.assertNotIn(
            "etc/zj_humanoid/device.env",
            {item["destination"] for item in config["extra_files"]},
        )

    def test_installer_skips_higher_top_level_version_and_never_forces_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            (staging / "DEBIAN").mkdir()
            common_builder.write_installer(
                staging, "navi-common-dep", ["install_common_deps.sh"]
            )
            installer = (staging / "usr/sbin/install_common_deps.sh").read_text(encoding="utf-8")

        self.assertIn('dpkg --compare-versions "$installed_version" ge "$payload_version"', installer)
        self.assertIn("required_packages+=(\"$package_name\")", installer)
        self.assertIn("requested-packages.tsv", installer)
        self.assertIn("deb [trusted=yes] file:%s ./", installer)
        self.assertIn(
            'apt-get -y --no-download --no-install-recommends "${apt_options[@]}" install "${required_packages[@]}"',
            installer,
        )
        self.assertNotIn('install "${payloads[@]}"', installer)
        self.assertNotIn("--allow-downgrades", installer)
        self.assertNotIn("validate-config", installer)
        self.assertNotIn("ROBOT_TYPE", installer)
        self.assertNotIn("deploy_common.py", installer)

    def test_common_carrier_guard_rejects_device_configuration_and_robot_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            (staging / "usr/sbin").mkdir(parents=True)
            (staging / "usr/sbin/install_common_deps.sh").write_text(
                "python3 deploy_common.py validate-config --target orin-humble\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "must not depend on device configuration"):
                common_builder.verify_common_carrier(staging)

    def test_local_repository_contains_full_closure_but_only_records_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload_directory = Path(temporary) / "payload"
            payload_directory.mkdir()
            for name, version in (("root-package", "1.0"), ("dependency-package", "1.0")):
                source = Path(temporary) / name
                (source / "DEBIAN").mkdir(parents=True)
                (source / "DEBIAN/control").write_text(
                    "Package: {}\nVersion: {}\nArchitecture: all\nMaintainer: Test <test@example.invalid>\n"
                    "Description: test package\n".format(name, version),
                    encoding="utf-8",
                )
                subprocess.run(
                    ["dpkg-deb", "--build", str(source), str(payload_directory / f"{name}.deb")],
                    check=True,
                )

            requested = common_builder.write_local_apt_repository(
                payload_directory,
                sorted(payload_directory.glob("*.deb")),
                [("root-package", "0")],
            )

            self.assertEqual(requested, [{"name": "root-package", "version": "1.0"}])
            self.assertEqual(
                (payload_directory / "requested-packages.tsv").read_text(encoding="utf-8"),
                "root-package\t1.0\n",
            )
            index = (payload_directory / "Packages").read_text(encoding="utf-8")
            self.assertIn("Package: root-package", index)
            self.assertIn("Package: dependency-package", index)

    def test_builder_rejects_a_payload_target_from_a_different_os_release(self) -> None:
        target = {
            "os_id": "ubuntu", "os_version": "20.04", "architecture": "amd64", "machine": "x86_64",
        }
        with patch.object(common_builder, "host_release", return_value={"ID": "ubuntu", "VERSION_ID": "22.04"}), \
             patch.object(common_builder.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "amd64\n")), \
             patch.object(common_builder.os, "uname", return_value=SimpleNamespace(machine="x86_64")):
            with self.assertRaisesRegex(RuntimeError, "target-matched Common builder"):
                common_builder.validate_build_host({"id": "pico-common"}, target)


if __name__ == "__main__":
    unittest.main()
