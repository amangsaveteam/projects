#!/usr/bin/env python3
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_offline_common_bundle", ROOT / "common/build_offline_common_bundle.py"
)
bundle = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bundle)


class OfflineClosureDownloadTest(unittest.TestCase):
    def test_installer_uses_apt_to_order_offline_predepends(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            bundle.write_installer(
                staging,
                "navi-common-dep",
                ["install_common_deps.sh"],
                "orin-humble",
            )

            script = (staging / "usr/sbin/install_common_deps.sh").read_text(encoding="utf-8")

        self.assertIn("apt-get -y --no-download --no-install-recommends install", script)
        self.assertNotIn("apt-get -y --fix-broken", script)
        self.assertNotIn('dpkg -i "${payloads[@]}"', script)

    def test_installer_only_selects_missing_or_older_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            bundle.write_installer(
                staging,
                "navi-common-dep",
                ["install_common_deps.sh"],
                "orin-humble",
            )

            script = (staging / "usr/sbin/install_common_deps.sh").read_text(encoding="utf-8")

        self.assertIn("dpkg-deb -f \"$payload\" Package", script)
        self.assertIn("dpkg-deb -f \"$payload\" Version", script)
        self.assertIn("dpkg --compare-versions \"$installed_version\" ge \"$payload_version\"", script)
        self.assertIn('required_payloads+=("$payload")', script)
        self.assertIn('install "${required_payloads[@]}"', script)
        self.assertIn("must never downgrade", script)

    def test_environment_only_installer_accepts_an_empty_payload_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            bundle.write_installer(
                staging,
                "navi-pico-common-dep",
                ["configure_pico_jazzy_environment.sh"],
                "pico-jazzy",
                environment_only=True,
            )

            script = (staging / "usr/sbin/configure_pico_jazzy_environment.sh").read_text(encoding="utf-8")

        self.assertIn("Environment-only carrier", script)
        self.assertIn("no dependency payloads were installed", script)
        self.assertIn("exit 0", script)
        self.assertNotIn("apt-get", script)
        self.assertNotIn("*.deb", script)

    def test_empty_manifest_is_limited_to_environment_only_builds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "apt-packages.tsv"
            manifest.write_text("# intentionally empty\n", encoding="utf-8")

            self.assertEqual(bundle.parse_manifest(manifest, allow_empty=True), [])
            with self.assertRaises(ValueError):
                bundle.parse_manifest(manifest)

    def test_uses_empty_apt_status_and_keeps_transitive_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            calls = []

            def fake_run(command, **kwargs):
                calls.append(command)
                (directory / "requested.deb").write_bytes(b"requested")
                (directory / "transitive.deb").write_bytes(b"transitive")
                return subprocess.CompletedProcess(command, 0)

            fields = {
                "requested.deb": {
                    "Package": "requested", "Version": "1.2", "Architecture": "arm64", "Essential": "no", "Priority": "optional"
                },
                "transitive.deb": {
                    "Package": "transitive", "Version": "2.0", "Architecture": "all", "Essential": "no", "Priority": "optional"
                },
            }
            with patch.object(bundle.subprocess, "run", side_effect=fake_run), patch.object(
                bundle, "deb_field", side_effect=lambda path, field: fields[path.name][field]
            ):
                payloads = bundle.download_payloads([("requested", "1.0")], "arm64", directory)

            self.assertEqual([payload.name for payload in payloads], ["requested.deb", "transitive.deb"])
            command = calls[0]
            self.assertIn("--download-only", command)
            self.assertIn("--no-install-recommends", command)
            self.assertIn("APT::Architecture=arm64", command)
            self.assertIn("Acquire::ForceIPv4=true", command)
            self.assertIn("Dir::State::status={}".format(directory / "apt-status"), command)
            self.assertIn("requested", command)


if __name__ == "__main__":
    unittest.main()
