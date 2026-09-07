#!/usr/bin/env python3
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_orin_humble_stack", ROOT / "orin_humble_stack/build_orin_humble_stack.py"
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class OrinHumbleStackTest(unittest.TestCase):
    def test_builds_offline_bundle_with_all_supervisor_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            artifacts = directory / "artifacts"
            generated = directory / "generated"
            artifacts.mkdir()
            generated.mkdir()
            for filename in builder.ARTIFACTS.values():
                (artifacts / filename).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            chassis, agent = generated / "chassis.run", generated / "agent.run"
            chassis.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            agent.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            original = builder.GENERATED
            original_manifest = builder.MANIFEST
            manifest = json.loads(original_manifest.read_text(encoding="utf-8"))
            for module in manifest["modules"]:
                module["artifact"].pop("sha256", None)
                module["artifact"]["url"] = "local://output/{}.run".format(module["id"])
            test_manifest = directory / "orin-humble.json"
            test_manifest.write_text(json.dumps(manifest), encoding="utf-8")
            builder.GENERATED = {"chassis": chassis, "agent": agent}
            builder.MANIFEST = test_manifest
            try:
                output = builder.build(artifacts, directory / "out")
            finally:
                builder.GENERATED = original
                builder.MANIFEST = original_manifest
            payload = output.read_bytes().split(b"\n", 9)[9]
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                names = archive.getnames()
                install = archive.extractfile("install.sh").read().decode("utf-8")
                audio_installer = archive.extractfile(
                    "helpers/install_audio_without_start.py"
                ).read().decode("utf-8")
                audio_launch = archive.extractfile(
                    "generated/audio/launch.sh"
                ).read().decode("utf-8")
                robot_entrypoint = archive.extractfile(
                    "generated/robot/supervisor-entrypoint.sh"
                ).read().decode("utf-8")

        self.assertEqual(
            {"payloads/sensor.run", "payloads/robot.run", "payloads/audio.run", "payloads/chassis.run", "payloads/agent.run"},
            {name for name in names if name.startswith("payloads/") and name.endswith(".run")},
        )
        self.assertIn("configure_sensor_rpc.py", install)
        self.assertIn("disable --now navi-supervisor-agent.service", install)
        self.assertIn("navi-orin-audio-supervisor.service", install)
        self.assertIn("navi-orin-robot-supervisor.service", install)
        self.assertIn("port=192.168.217.100:19002", robot_entrypoint)
        self.assertIn("pwdlib 0\\\\.3\\\\.1", audio_installer)
        self.assertIn("known Audio pip-check compatibility patch", audio_installer)
        self.assertIn("migrate_legacy_avvtn_log", audio_installer)
        self.assertIn("Preserved legacy AVVTN log directory", audio_installer)
        self.assertIn("set -eo pipefail", audio_launch)
        self.assertNotIn("set -euo pipefail", audio_launch)
        self.assertIn('mic_device:=plughw:3,0', audio_launch)
        self.assertIn('spk_device:=plughw:2,0', audio_launch)
        self.assertNotIn('mic_device:="$MIC_DEVICE"', audio_launch)
        self.assertIn("export HOME=/var/lib/navi-audio", audio_launch)
        self.assertIn("export ROS_LOG_DIR=/var/log/naviai/audio/ros", audio_launch)
        self.assertIn("source /etc/naviai/Middleware.env", audio_launch)
        self.assertIn('export ROS_DOMAIN_ID="$platform_ros_domain_id"', audio_launch)
        self.assertIn('export CYCLONEDDS_URI="$platform_cyclonedds_uri"', audio_launch)
        self.assertIn("pico_uri:=ws://192.168.217.66:8765", audio_launch)
        self.assertIn("enable_background_task_test:=true", audio_launch)


if __name__ == "__main__":
    unittest.main()
