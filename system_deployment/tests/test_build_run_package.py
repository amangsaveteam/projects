#!/usr/bin/env python3
import hashlib
import importlib.util
import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_run_package", ROOT / "build_run_package.py")
build_run_package = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_run_package)


class BuildRunPackageTest(unittest.TestCase):
    def write_manifest(self, directory: Path, checksum: str) -> Path:
        content = """{
          // The builder accepts the one-stop-upgrade JSONC style.
          "version": "v1.2.3",
          "build_time": "2026-08-24 12:00:00",
          "branch_name": "release/test",
          "commit_id": "abcdef123",
          "ORIN": {
            "sys_env_version": "ubuntu-24.04",
            "modules": [
              {"name": "base-runtime", "version": "1", "url": "local://inputs/base.deb", "image": "", "dst": "base", "dependencies": []},
              {"name": "robot-runtime", "version": "2", "url": "local://inputs/robot.deb", "image": "", "dst": "runtime", "dependencies": ["base-runtime"], "sha256": "__CHECKSUM__"},
            ],
            "resource": [{"local_path": "config/orin.yaml", "path": "/etc/naviai/orin.yaml"}],
            "scripts": {"pre_install": [{"name": "before", "cmd": "echo before"}], "post_install": [], "pre_uninstall": [], "post_uninstall": []},
          },
          "PICO": {
            "modules": [{"name": "pico-runtime", "version": "3", "url": "local://inputs/pico.deb", "image": "", "dst": "base", "dependencies": []}],
            "resource": [{"local_path": "config/pico.yaml", "device_path": "/etc/nav01/pico.yaml"}],
            "scripts": {"pre_install": [], "post_install": [], "pre_uninstall": [], "post_uninstall": []},
          },
        }"""
        path = directory / "package.json"
        path.write_text(content.replace("__CHECKSUM__", checksum), encoding="utf-8")
        return path

    def read_payload(self, package: Path):
        data = package.read_bytes()
        return tarfile.open(fileobj=io.BytesIO(data[len(build_run_package.run_header()):]), mode="r:gz")

    def test_builds_platform_specific_payload_and_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "inputs").mkdir()
            (directory / "config").mkdir()
            (directory / "inputs/base.deb").write_bytes(b"base")
            robot = directory / "inputs/robot.deb"
            robot.write_bytes(b"robot")
            (directory / "inputs/pico.deb").write_bytes(b"pico")
            (directory / "config/orin.yaml").write_text("orin: true\n", encoding="utf-8")
            (directory / "config/pico.yaml").write_text("pico: true\n", encoding="utf-8")
            manifest = self.write_manifest(directory, hashlib.sha256(robot.read_bytes()).hexdigest())

            output = build_run_package.build(manifest, directory / "out")
            self.assertEqual(output.name, "Middleware_v1.2.3_release_test_abcdef12_20260824.run")
            help_output = subprocess.run([str(output), "--help"], check=True, text=True, capture_output=True)
            self.assertIn("install|uninstall", help_output.stdout)
            with self.read_payload(output) as payload:
                names = payload.getnames()
                self.assertIn("ORIN/.dists/base/000-base-runtime.deb", names)
                self.assertIn("ORIN/.dists/runtime/001-robot-runtime.deb", names)
                self.assertIn("PICO/.dists/base/000-pico-runtime.deb", names)
                self.assertIn("ORIN/resources/000-orin.yaml", names)
                self.assertIn("PICO/resources/000-pico.yaml", names)
                orin_script = payload.extractfile("ORIN/run.sh").read().decode("utf-8")
                pico_script = payload.extractfile("PICO/run.sh").read().decode("utf-8")
                launcher = payload.extractfile("launcher.sh").read().decode("utf-8")
            self.assertLess(orin_script.index("000-base-runtime.deb"), orin_script.index("001-robot-runtime.deb"))
            self.assertLess(orin_script.index("dpkg -r robot-runtime"), orin_script.index("dpkg -r base-runtime"))
            self.assertIn("/etc/naviai/Middleware.env", orin_script)
            self.assertIn("/etc/nav01/Middleware.env", pico_script)
            self.assertIn("/etc/profile.d/zj_humanoid.sh", orin_script)
            self.assertIn("ZJ_PROFILE_ENV_ONLY=1", orin_script)
            self.assertIn("MIDDLEWARE_ROS_DISTRO", orin_script)
            self.assertIn('/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash', orin_script)
            self.assertIn('export MIDDLEWARE_ROS_DISTRO="humble"', pico_script)
            self.assertIn("export MIDDLEWARE_VERSION=v1.2.3", orin_script)
            self.assertLess(orin_script.index("mv \"$env_tmp\" /etc/naviai/Middleware.env"), orin_script.index("dpkg -i \"$package_root/ORIN/.dists/base/000-base-runtime.deb\""))
            self.assertIn('"$package_root/ORIN/.dists/base/000-base-runtime.deb"', orin_script)
            self.assertIn("--device ORIN|PICO", launcher)

    def test_rejects_checksum_mismatch_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "inputs").mkdir()
            (directory / "config").mkdir()
            for name in ("base.deb", "robot.deb", "pico.deb"):
                (directory / "inputs" / name).write_bytes(b"payload")
            (directory / "config/orin.yaml").write_text("x\n", encoding="utf-8")
            (directory / "config/pico.yaml").write_text("x\n", encoding="utf-8")
            manifest = self.write_manifest(directory, "0" * 64)
            with self.assertRaises(build_run_package.PackageBuildError):
                build_run_package.build(manifest, directory / "out")
            self.assertFalse((directory / "out").exists())

    def test_rejects_wrong_resource_path_for_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "config").mkdir()
            (directory / "config/value").write_text("x\n", encoding="utf-8")
            manifest = directory / "package.json"
            manifest.write_text('{"version":"v1","ORIN":{"resource":[{"local_path":"config/value","device_path":"/etc/x"}]}}', encoding="utf-8")
            with self.assertRaises(build_run_package.PackageBuildError):
                build_run_package.build(manifest, directory / "out", dry_run=True)

    def test_builds_rdk_payload_and_runtime_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "inputs").mkdir()
            (directory / "config").mkdir()
            (directory / "inputs/rdk.deb").write_bytes(b"rdk")
            (directory / "config/rdk.yaml").write_text("rdk: true\n", encoding="utf-8")
            manifest = directory / "package.json"
            manifest.write_text(
                '''{
                  "version": "v1",
                  "build_time": "2026-08-25",
                  "RDK": {
                    "sys_env_version": "RDK-OS-V5.1.0",
                    "modules": [{"name": "rdk-runtime", "version": "1", "url": "local://inputs/rdk.deb", "image": "", "dependencies": []}],
                    "resource": [{"local_path": "config/rdk.yaml", "path": "/etc/naviai/rdk.yaml"}],
                    "scripts": {"pre_install": [], "post_install": [], "pre_uninstall": [], "post_uninstall": []}
                  }
                }''',
                encoding="utf-8",
            )
            output = build_run_package.build(manifest, directory / "out")
            with self.read_payload(output) as payload:
                names = payload.getnames()
                rdk_script = payload.extractfile("RDK/run.sh").read().decode("utf-8")
                launcher = payload.extractfile("launcher.sh").read().decode("utf-8")
            self.assertIn("RDK/.dists/000-rdk-runtime.deb", names)
            self.assertIn("RDK/resources/000-rdk.yaml", names)
            self.assertIn("/etc/naviai/Middleware.env", rdk_script)
            self.assertIn("ROSDEP_OS_OVERRIDE", rdk_script)
            self.assertIn("ROS_OS_OVERRIDE", rdk_script)
            self.assertIn("--device ORIN|PICO|RDK", launcher)


if __name__ == "__main__":
    unittest.main()
