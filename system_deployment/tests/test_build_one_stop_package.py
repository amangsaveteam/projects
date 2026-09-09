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
    def test_pico_common_is_downloaded_before_module_dependencies(self) -> None:
        target = builder.load(ROOT / "one_stop/package-urls.json")["targets"]["pico-humble"]
        extras = target["extra_debs"]
        self.assertEqual([item["name"] for item in extras[:3]], ["pico-common", "upperlimb-common", "robot"])
        self.assertEqual(
            extras[0]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/pico/develop/"
            "navi_pico_common_dep-2.0.0-release-humble-amd64.deb",
        )

    def test_pico_payload_has_a_runtime_platform_guard(self) -> None:
        script = builder.target_install(
            "pico-humble", "payloads/pico-humble/system-config", "", None,
            [("payloads/pico-humble/extra-00.deb", ["/usr/sbin/install_pico_common_deps.sh"], [], None)],
            [], [], target_platform=("ubuntu", "20.04", "amd64"),
        )

        self.assertIn('"${ID,,}" != ubuntu', script)
        self.assertIn('"${VERSION_ID:-}" != 20.04', script)
        self.assertIn('"$actual_arch" != amd64', script)
        self.assertIn("pico-humble payloads require ubuntu 20.04 / amd64", script)
        self.assertLess(
            script.index("pico-humble payloads require"),
            script.index('dpkg -i "$root/payloads/pico-humble/extra-00.deb"'),
        )

    def test_environment_and_robot_type_are_configured_before_common_payloads(self) -> None:
        setup = builder.system_config_installer(
            "pico-humble", "pico-humble", "payloads/pico-humble/system-config"
        )
        install = builder.target_install(
            "pico-humble", "payloads/pico-humble/system-config", "", None,
            [("payloads/pico-humble/extra-00.deb", ["/usr/sbin/install_pico_common_deps.sh"], [], None)],
            [], [],
        )

        self.assertLess(setup.index("cyclonedds.xml"), setup.index("Middleware.env"))
        self.assertLess(setup.index("Middleware.env"), setup.index("deploy_common.py\" configure"))
        self.assertIn('robot_type="${1:?robot type is required}"', setup)
        self.assertLess(
            install.index("install-system-config.sh"),
            install.index("dpkg -i \"$root/payloads/pico-humble/extra-00.deb\""),
        )
        self.assertLess(
            install.index("dpkg -i \"$root/payloads/pico-humble/extra-00.deb\""),
            install.index("/usr/sbin/install_pico_common_deps.sh"),
        )

    def test_orin_run_arguments_match_each_embedded_installer_interface(self) -> None:
        config = builder.load(ROOT / "one_stop/package-urls.json")
        for target_name in ("orin-humble", "orin-jazzy"):
            runs = {
                item["name"]: item["arguments"]
                for item in config["targets"][target_name]["runs"]
            }
            self.assertEqual(runs["chassis"], ["--", "--robot-type", "{robot_type}"])
            self.assertEqual(runs["sensor"], ["--", "--robot-type", "{robot_type}"])
            self.assertEqual(runs["robot"], [])
            self.assertEqual(runs["audio"], [])
            self.assertEqual(runs["vision"], [])

    def test_extra_installer_environment_is_scoped_to_that_installer(self) -> None:
        script = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [("payloads/orin-humble/extra-02.deb", ["/usr/lib/orin-robot-common-deb/install_robot_deps.sh"], ["PIP_NO_BUILD_ISOLATION=1"], None)],
            [], [],
        )

        self.assertIn(
            'env PIP_NO_BUILD_ISOLATION=1 "/usr/lib/orin-robot-common-deb/install_robot_deps.sh"',
            script,
        )
        self.assertEqual(builder.resolve_environment({"PIP_NO_BUILD_ISOLATION": "1"}, "test"), ["PIP_NO_BUILD_ISOLATION=1"])
        with self.assertRaises(builder.BuildError):
            builder.resolve_environment({"invalid-name": "1"}, "test")

    def test_auto_installer_reports_an_invalid_debian_archive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = Path(temporary) / "not-a-deb"
            payload.write_text("gateway error", encoding="utf-8")
            with self.assertRaisesRegex(builder.BuildError, "extra.installers is not a readable Debian package"):
                builder.resolve_installers(payload, ["auto"], "extra.installers", False)

    def test_orin_humble_audio_is_installed_only_by_its_vendor_run_package(self) -> None:
        config = builder.load(ROOT / "one_stop/package-urls.json")
        humble = config["targets"]["orin-humble"]
        self.assertNotIn("audio", {item["name"] for item in humble["extra_debs"]})
        self.assertNotIn("audio-module", {item["name"] for item in humble["extra_debs"]})
        audio = next(item for item in humble["runs"] if item["name"] == "audio")
        self.assertEqual(audio["arguments"], [])
        self.assertEqual(
            audio["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/audio/humble/"
            "navi_audio_installer-2.0.0-release-humble-arm64.run",
        )

    def test_orin_humble_installs_sensor_parent_bundle_first(self) -> None:
        target = builder.load(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        extras = target["extra_debs"]
        self.assertEqual([item["name"] for item in extras[:3]], ["orin-common", "sensor", "robot"])
        self.assertTrue(target["sensor_parent_compatibility"])
        self.assertEqual(
            extras[0]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/orin/develop/"
            "navi_common_dep-2.0.0-release-humble-arm64.deb",
        )
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            checksums = []
            compatibility = builder.stage_sensor_parent_compatibility(
                stage, "orin-humble", target, checksums, False
            )
            compatibility_deb = stage / compatibility
            self.assertEqual(
                subprocess.check_output(["dpkg-deb", "-f", str(compatibility_deb), "Package"], text=True).strip(),
                "orin-common-deb",
            )
            unpacked = stage / "unpacked"
            subprocess.run(["dpkg-deb", "-x", str(compatibility_deb), str(unpacked)], check=True)
            wrapper = (unpacked / "usr/lib/orin-common-deb/install_deps.sh").read_text(encoding="utf-8")
            self.assertIn("/usr/sbin/install_common_deps.sh", wrapper)
            self.assertIn("--verify-only", wrapper)
        install = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [
                ("payloads/orin-humble/extra-00.deb", ["/usr/lib/orin-common-deb/install_deps.sh"], [], None),
                ("payloads/orin-humble/orin-common-deb-compat.deb", [], [], None),
                ("payloads/orin-humble/extra-01.deb", ["/usr/lib/orin-sensor-common-deb/install_deps.sh"], [], None),
            ], [], [],
        )
        self.assertLess(
            install.index('/usr/lib/orin-common-deb/install_deps.sh'),
            install.index('/usr/lib/orin-sensor-common-deb/install_deps.sh'),
        )
        self.assertLess(
            install.index('orin-common-deb-compat.deb'),
            install.index('/usr/lib/orin-sensor-common-deb/install_deps.sh'),
        )

    def test_vision_is_installed_only_by_its_vendor_run_package(self) -> None:
        config = builder.load(ROOT / "one_stop/package-urls.json")
        for target_name in ("orin-humble", "orin-jazzy"):
            target = config["targets"][target_name]
            self.assertNotIn("vision", {item["name"] for item in target["extra_debs"]})
            self.assertIn("vision", {item["name"] for item in target["runs"]})

    def test_system_python_contract_is_checked_before_deb_install(self) -> None:
        contract = builder.resolve_system_python_contract({
            "user": "naviai", "module": "torch",
            "version": "2.5.0a0+872d972e41.nv24.08", "cuda": "12.6",
        }, "orin-humble.vision")
        script = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [("payloads/orin-humble/extra-04.deb", ["/usr/lib/orin-vision-common-deb/install_vision_deps.sh"], [], contract)],
            [], [],
        )

        self.assertLess(script.index("Checking system Python contract"), script.index("dpkg -i \"$root/payloads/orin-humble/extra-04.deb\""))
        self.assertIn("runuser -u naviai", script)
        self.assertIn("module.cuda.is_available", script)
        self.assertIn("do not run apt --fix-broken install", script)

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

    def test_vision_supervisor_uses_the_documented_isolated_dds_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            checksums = []
            startup = builder.stage_vision_supervisor(
                stage,
                "orin-jazzy",
                builder.vision_supervisor("orin-jazzy", {
                    "device": "ORIN",
                    "vision_supervisor": {
                        "service": "navi-vision-supervisor.service",
                        "ros_distro": "jazzy",
                    },
                }),
                checksums,
                False,
            )
            launch = (stage / startup[0][1]).read_text(encoding="utf-8")
            service = (stage / startup[0][2]).read_text(encoding="utf-8")
            install = builder.target_install(
                "orin-jazzy", "payloads/orin-jazzy/system-config", "", None,
                [], [("payloads/orin-jazzy/run-04.run", [])],
                ["navi-vision-supervisor.service"], startup,
            )

        self.assertIn("source /opt/ros/jazzy/setup.bash", launch)
        self.assertIn("source /opt/naviai/venvs/vision/bin/activate", launch)
        self.assertIn("export ROS_DOMAIN_ID=72", launch)
        self.assertIn("unset CYCLONEDDS_URI", launch)
        self.assertIn("selected_camera:=auto camera_auto_timeout_sec:=8.0", launch)
        self.assertIn("ExecStart=/bin/bash /usr/local/lib/navi-vision/navi-vision-supervisor-launch.sh", service)
        self.assertIn('"$root/payloads/orin-jazzy/run-04.run"', install)
        self.assertNotIn('run-04.run" -- --robot-type', install)
        self.assertIn("/etc/systemd/system/navi-vision-supervisor.service", install)

    def test_launcher_preserves_environment_and_virtualenv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.sh"
            environment.write_text("export TEST_AUDIO_ENV=ready\n")
            script = builder.supervisor_launch_script({
                "working_directory": temporary,
                "source_files": [str(environment)],
                "prelude": [
                    "export ROS_LOG_DIR=/tmp/audio-regression-logs",
                    "test \"$ROS_LOG_DIR\" = /tmp/audio-regression-logs",
                    "! shopt -q login_shell",
                ],
                "command": "test \"$TEST_AUDIO_ENV\" = ready",
            })
            subprocess.run(["bash", "-c", script], check=True)
            self.assertIn('exec test "$TEST_AUDIO_ENV" = ready', script)
            self.assertNotIn("/bin/bash -c", script)

    def test_orin_supervisor_modules_are_generated_from_the_only_delivery_config(self) -> None:
        config = builder.load(ROOT / "one_stop/package-urls.json")
        target = config["targets"]["orin-humble"]
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            checksums = []
            startup, registrations, post_install, paths = builder.stage_supervisor_modules(
                stage, "orin-humble", target, checksums, False
            )
            agent = builder.stage_supervisor_agent(stage, "orin-humble", paths, checksums, False)
            script = builder.target_install(
                "orin-humble", "payloads/orin-humble/system-config", "", None, [], [],
                [item[0] for item in startup], supervisor_startup=startup,
                registrations=registrations, post_install=post_install, agent_service=paths, agent_payload=agent,
            )

            robot_entrypoint = (stage / "targets/orin-humble/supervisor/robot/supervisor-entrypoint.sh").read_text(encoding="utf-8")
            audio_launch = (stage / "targets/orin-humble/supervisor/audio/launch.sh").read_text(encoding="utf-8")
            robot_unit = (stage / "targets/orin-humble/supervisor/robot/navi-orin-robot-supervisor.service").read_text(encoding="utf-8")
            robot_registration = (stage / "targets/orin-humble/supervisor/modules/robot.json").read_text(encoding="utf-8")
            agent_unit_exists = (stage / "targets/orin-humble/supervisor-agent/navi-orin-supervisor-agent.service").is_file()

        self.assertEqual(
            {item[0] for item in startup},
            {
                "navi-orin-chassis.service",
                "navi-orin-robot-supervisor.service",
                "navi-orin-audio-supervisor.service",
                "navi-vision-supervisor.service",
            },
        )
        self.assertIn("port=192.168.217.100:19002", robot_entrypoint)
        self.assertIn("supervisor.rpcinterface:make_main_rpcinterface", robot_entrypoint)
        self.assertIn("unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH", audio_launch)
        self.assertIn("source /opt/naviai/venvs/audio/bin/activate", audio_launch)
        self.assertIn("export ROS_LOG_DIR=/var/log/naviai/audio/ros", audio_launch)
        self.assertIn('install -d -m 0755 "$HOME" "$ROS_HOME" "$ROS_LOG_DIR"', audio_launch)
        self.assertIn("pico_gateway_url:=ws://192.168.217.66:8765", audio_launch)
        self.assertIn("ExecStart=/bin/bash /etc/naviai/supervised-stack/robot/supervisor-entrypoint.sh", robot_unit)
        self.assertIn("http://192.168.217.100:19002/RPC2", robot_registration)
        self.assertIn("/etc/naviai/supervisor-agent/modules.d/robot.json", script)
        self.assertNotIn("configure_sensor_rpc.py", script)
        self.assertIn("systemctl restart navi-sensor-host.service", script)
        self.assertIn("systemctl restart navi-orin-supervisor-agent.service", script)
        self.assertTrue(agent_unit_exists)
        self.assertIn("/etc/naviai/supervisor-agent/modules.d/vision.json", script)
        self.assertNotIn("/bin/bash -lc", audio_launch)
        self.assertIn("exec ros2 launch navi_audio_pkg audio_bringup.launch.py", audio_launch)

    def test_pico_native_supervisor_modules_are_registered_without_config_injection(self) -> None:
        config = builder.load(ROOT / "one_stop/package-urls.json")
        target = config["targets"]["pico-humble"]
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            checksums = []
            startup, registrations, post_install, paths = builder.stage_supervisor_modules(
                stage, "pico-humble", target, checksums, False
            )
            agent = builder.stage_supervisor_agent(stage, "pico-humble", paths, checksums, False)
            script = builder.target_install(
                "pico-humble", "payloads/pico-humble/system-config", "", None, [], [],
                target["managed_services"], supervisor_startup=startup, registrations=registrations,
                post_install=post_install, agent_service=paths, agent_payload=agent,
            )
            robot = (stage / "targets/pico-humble/supervisor/modules/robot.json").read_text(encoding="utf-8")
            upperlimb = (stage / "targets/pico-humble/supervisor/modules/upperlimb.json").read_text(encoding="utf-8")

        self.assertEqual(startup, [])
        self.assertEqual(
            json.loads(robot)["modules"]["robot"]["endpoint"],
            "http://192.168.217.66:19002/RPC2",
        )
        self.assertEqual(
            json.loads(upperlimb)["modules"]["upperlimb"]["endpoint"],
            "http://192.168.217.66:19003/RPC2",
        )
        self.assertIn("navi-pico-robot-supervisor.service", script)
        self.assertIn("navi-pico-upperlimb.service", script)
        self.assertIn("/etc/nav01/supervisor-agent/modules.d/robot.json", script)
        self.assertIn("/etc/nav01/supervisor-agent/modules.d/upperlimb.json", script)
        self.assertNotIn("configure_sensor_rpc.py", script)


if __name__ == "__main__":
    unittest.main()
