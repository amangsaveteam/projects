#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_one_stop_package", ROOT / "one_stop/build_one_stop_package.py")
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)
HELPER_SPEC = importlib.util.spec_from_file_location(
    "install_run_without_final_exec", ROOT / "one_stop/install_run_without_final_exec.py"
)
audio_install_helper = importlib.util.module_from_spec(HELPER_SPEC)
assert HELPER_SPEC.loader is not None
HELPER_SPEC.loader.exec_module(audio_install_helper)


class OneStopPackageTest(unittest.TestCase):
    def test_archive_wrapper_cleans_extraction_on_success_and_failure(self):
        for status in (0, 17):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                payload = io.BytesIO()
                script = ('#!/bin/sh\nexit %d\n' % status).encode()
                with tarfile.open(fileobj=payload, mode='w:gz') as archive:
                    member = tarfile.TarInfo('install.sh')
                    member.mode = 0o755
                    member.size = len(script)
                    archive.addfile(member, io.BytesIO(script))
                run = root / 'test.run'
                run.write_bytes(builder.header() + payload.getvalue())
                result = subprocess.run(['sh', str(run)], env={**os.environ, 'TMPDIR': str(root)})
                self.assertEqual(result.returncode, status)
                self.assertEqual(list(root.iterdir()), [run])

    def test_failed_archive_write_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = root / 'version.json'
            urls = root / 'urls.json'
            version.write_text(json.dumps({'schema_version': 1, 'version': '2.0.0', 'output_name': 'test'}))
            urls.write_text(json.dumps({'schema_version': 1, 'targets': {'pico-jazzy': {
                'os_id': 'ubuntu', 'os_version': '24.04', 'architecture': 'amd64',
                'system_config': {'configure_target': 'pico-jazzy', 'base_image_contract': 'pico-jazzy'},
                'extra_debs': [], 'runs': [],
            }}}))
            output = root / 'out'
            output.mkdir()
            previous = output / 'test.run'
            previous.write_bytes(b'previous release')
            with patch.object(builder.tarfile, 'open', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    builder.build(version, urls, output)
            self.assertEqual(list(output.iterdir()), [previous])
            self.assertEqual(previous.read_bytes(), b'previous release')

    def test_robot_working_directory_exists_before_vendor_installer(self):
        script = builder.target_install('orin-humble', 'system-config', '', None, [],
                                        [('payloads/robot.run', [])], [])
        self.assertLess(script.index('install -d -m 0755 /var/lib/navi'), script.index('"$root/payloads/robot.run"'))

    def test_audio_helper_suppresses_vendor_launch_line_variants(self) -> None:
        for launch in (
            'exec ros2 launch navi_audio_pkg audio_bringup.launch.py "${AUDIO_LAUNCH_ARGS[@]}"',
            'exec_as_runtime_user ros2 launch navi_audio_pkg audio_bringup.launch.py "${AUDIO_LAUNCH_ARGS[@]}"',
            'ros2 launch navi_audio_pkg audio_bringup.launch.py selected_camera:=auto',
        ):
            installer = "prepare_dependencies\n{}\n".format(launch)
            rewritten = audio_install_helper.suppress_final_audio_launch(installer)
            self.assertIn(audio_install_helper.REPLACEMENT, rewritten)
            self.assertNotIn("audio_bringup.launch.py", rewritten)

    def test_audio_helper_keeps_install_steps_without_running_runtime_user_launch(self) -> None:
        installer = '''#!/bin/bash
set -eu
exec_as_runtime_user() { echo UNEXPECTED_LAUNCH; exit 99; }
echo INSTALL
echo VERIFY
AUDIO_LAUNCH_ARGS=()
exec_as_runtime_user ros2 launch navi_audio_pkg audio_bringup.launch.py "${AUDIO_LAUNCH_ARGS[@]}"
'''
        result = subprocess.run(
            ["bash", "-c", audio_install_helper.suppress_final_audio_launch(installer)],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(result.stdout.splitlines(), [
            "INSTALL", "VERIFY",
            "Audio installed; startup is managed by zj-humanoid-orin-audio-supervisor.service",
        ])

    def test_audio_helper_rejects_ambiguous_or_missing_vendor_launch(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "found 0"):
            audio_install_helper.suppress_final_audio_launch("echo no launch\n")
        with self.assertRaisesRegex(RuntimeError, "found 2"):
            audio_install_helper.suppress_final_audio_launch(
                "ros2 launch navi_audio_pkg audio_bringup.launch.py\n"
                "ros2 launch navi_audio_pkg audio_bringup.launch.py\n"
            )

    def test_audio_helper_fallback_guard_only_suppresses_the_audio_launch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "unexpected-launch"
            installer = directory / "install.sh"
            installer.write_text(
                "ros2() { touch \"$1\"; }\n"
                "exec ros2 launch navi_audio_pkg audio_bringup.launch.py\n",
                encoding="utf-8",
            )
            audio_install_helper.run_with_audio_launch_guard(installer, [str(marker)], directory)
            self.assertFalse(marker.exists())

    def test_common_debs_are_downloaded_from_the_artifact_server_not_built_in_one_stop(self) -> None:
        targets = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]
        self.assertEqual(
            targets["orin-humble"]["extra_debs"][0]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/orin/develop/"
            "navi_common_dep-2.0.0-release-humble-arm64.deb",
        )
        self.assertEqual(
            targets["pico-humble"]["extra_debs"][0]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/pico/develop/"
            "navi_pico_common_dep-2.0.0-release-humble-amd64.deb",
        )
        self.assertNotIn("common_builds", targets["orin-humble"])
        self.assertNotIn("common_builds", targets["pico-humble"])

    def test_pico_common_is_downloaded_before_module_dependencies(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["pico-humble"]
        extras = target["extra_debs"]
        self.assertEqual([item["name"] for item in extras[:3]], ["pico-common", "upperlimb-common", "robot-common-dep"])
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
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
        for target_name in ("orin-humble", "orin-jazzy"):
            runs = {
                item["name"]: item["arguments"]
                for item in config["targets"][target_name]["runs"]
            }
            if target_name == "orin-jazzy":
                self.assertEqual(runs["chassis"], ["--", "--robot-type", "{robot_type}"])
            else:
                self.assertNotIn("chassis", runs)
            if target_name == "orin-humble":
                self.assertEqual(runs["manip"], ["--", "--force"])
            self.assertEqual(runs["sensor"], ["--", "--robot-type", "{robot_type}"])
            self.assertEqual(runs["robot"], [])
            self.assertEqual(runs["audio"], [])
            if target_name == "orin-jazzy":
                self.assertEqual(runs["vision"], [])
            else:
                self.assertEqual(runs["vision"], [])

    def test_pico_run_arguments_match_each_installer_interface(self) -> None:
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
        humble = {
            item["name"]: item["arguments"]
            for item in config["targets"]["pico-humble"]["runs"]
        }
        jazzy = {
            item["name"]: item["arguments"]
            for item in config["targets"]["pico-jazzy"]["runs"]
        }
        robot_type_arguments = ["--", "--robot-type", "{robot_type}"]
        self.assertEqual(humble, {"robot": [], "upperlimb": robot_type_arguments, "display": []})
        self.assertEqual(jazzy["upperlimb"], robot_type_arguments)

    def test_vendor_runs_inherit_and_cannot_replace_pico_shared_middleware(self) -> None:
        script = builder.target_install(
            "pico-humble", "payloads/pico-humble/system-config", "", None, [],
            [("payloads/pico-humble/run-upperlimb.run", ["--", "--robot-type", "{robot_type}"])], [],
        )
        install_config = '/bin/bash "$root/targets/pico-humble/install-system-config.sh" "$robot_type"'
        run = '/bin/bash "$root/payloads/pico-humble/run-upperlimb.run" -- --robot-type "$robot_type"'
        self.assertIn("source /etc/nav01/Middleware.env", script)
        # The command itself occurs once in the helper definition; its calls
        # are separate lines.  It runs once initially and once after the
        # vendor installer restores the canonical carrier.
        self.assertEqual(script.count(install_config), 1)
        self.assertEqual(script.count("\ninstall_system_config\n"), 2)
        self.assertLess(script.rfind("load_shared_middleware", 0, script.index(run)), script.index(run))
        self.assertGreater(script.rfind("\ninstall_system_config\n"), script.index(run))

    def test_orin_humble_robot_migrates_the_retired_monolithic_package_before_install(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        robot = next(item for item in target["runs"] if item["name"] == "robot")
        self.assertEqual(robot["remove_packages"], ["navi-robot-state"])
        install = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None, [],
            [("payloads/orin-humble/run-02.run", [], "vendor", None, ["navi-robot-state"])], [],
        )
        self.assertLess(
            install.index("dpkg --remove navi-robot-state"),
            install.index('/bin/bash "$root/payloads/orin-humble/run-02.run"'),
        )

    def test_run_package_removal_list_rejects_unsafe_package_names(self) -> None:
        with self.assertRaises(builder.BuildError):
            builder.resolve_run_remove_packages(["navi-robot-state; rm -rf /"], "test.run")

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
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
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

    def test_orin_humble_installs_cloud_common_and_legacy_identity_before_sensor(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        extras = target["extra_debs"]
        self.assertEqual(
            [item["name"] for item in extras[:4]],
            ["orin-common", "orin-common-compat", "sensor-common-dep", "robot-common-dep"],
        )
        self.assertNotIn("sensor_parent_compatibility", target)
        self.assertEqual(
            extras[0]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/orin/develop/"
            "navi_common_dep-2.0.0-release-humble-arm64.deb",
        )
        self.assertEqual(
            extras[1]["url"],
            "http://10.51.33.211:10000/chfs/shared/ros2_modules/common/orin/develop/"
            "orin_common_deb_2.0.0-release-humble-arm64.deb",
        )
        self.assertEqual(extras[1]["skip_if_package_installed"], "orin-common-deb")
        log = next(item for item in extras if item["name"] == "naviai-log")
        self.assertTrue(log["force_overwrite"])
        self.assertNotIn("install_group", log)
        install = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [
                ("payloads/orin-humble/extra-00.deb", ["/usr/sbin/install_common_deps.sh"], [], None),
                ("payloads/orin-humble/extra-01.deb", [], [], None),
                ("payloads/orin-humble/extra-02.deb", ["/usr/lib/orin-sensor-common-deb/install_deps.sh"], [], None),
            ], [], [],
        )
        self.assertLess(
            install.index('/usr/sbin/install_common_deps.sh'),
            install.index('/usr/lib/orin-sensor-common-deb/install_deps.sh'),
        )
        self.assertLess(
            install.index('dpkg -i "$root/payloads/orin-humble/extra-01.deb"'),
            install.index('/usr/lib/orin-sensor-common-deb/install_deps.sh'),
        )

    def test_force_overwrite_is_limited_to_an_ungrouped_deb(self) -> None:
        install = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None,
            [("payloads/orin-humble/extra-04.deb", [], [], None, None, [], None, True)], [], [],
        )
        self.assertIn('dpkg --force-overwrite -i "$root/payloads/orin-humble/extra-04.deb"', install)
        with self.assertRaisesRegex(builder.BuildError, "force_overwrite requires an extra_debs entry without install_group"):
            builder.target_install(
                "orin-humble", "payloads/orin-humble/system-config", "", None,
                [("payloads/orin-humble/extra-04.deb", [], [], None, "shared", [], None, True)], [], [],
            )

    def test_vision_is_installed_only_by_its_vendor_run_package_when_available(self) -> None:
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
        humble = config["targets"]["orin-humble"]
        jazzy = config["targets"]["orin-jazzy"]
        self.assertNotIn("vision", {item["name"] for item in humble["extra_debs"]})
        self.assertIn("vision", {item["name"] for item in humble["runs"]})
        self.assertIn("vision", {item["id"] for item in humble["supervisor_modules"]})
        self.assertNotIn("vision", {item["name"] for item in jazzy["extra_debs"]})
        self.assertIn("vision", {item["name"] for item in jazzy["runs"]})

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
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0-1", "output_name": "navi_one_stop_installer-2.0.0-1"}), encoding="utf-8")
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
                release = json.load(archive.extractfile("release-manifest.json"))
                target_release = json.load(archive.extractfile("targets/pico-jazzy/release-manifest.json"))
                install = archive.extractfile("targets/pico-jazzy/install.sh").read().decode("utf-8")
                pretest = archive.extractfile("targets/pico-jazzy/pretest.sh").read().decode("utf-8")
                system_install = archive.extractfile("targets/pico-jazzy/install-system-config.sh").read().decode("utf-8")
                master_install = archive.extractfile("install.sh").read().decode("utf-8")
                target_manifest = archive.extractfile("targets/pico-jazzy/payloads.sha256").read().decode("utf-8")

        self.assertIn("payloads/pico-jazzy/system-config/deploy_common.py", names)
        self.assertEqual(release["release"], "2.0.0-1")
        self.assertEqual(release["targets"]["pico-jazzy"], target_release)
        self.assertEqual(target_release["offline_installation"], "unverified")
        self.assertIn("release_state.py", target_release["payload_checksums"])
        self.assertIn("release-manifest.json", target_manifest)
        self.assertIn("navi_release fail", install)
        self.assertLess(install.index("navi_release begin"), install.index('/bin/bash "$root/targets/'))
        self.assertLess(install.index("navi_release complete"), install.index("install_complete=1"))
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
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0-1", "output_name": "navi_one_stop_installer-2.0.0-1"}), encoding="utf-8")
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
                        "runs": [{"name": "upperlimb", "version": "2.0.0-2", "url": run.as_uri(), "sha256": builder.file_sha256(run)}],
                    }
                },
            }), encoding="utf-8")

            output = builder.build(version, urls, directory / "out")
            info = subprocess.run([str(output), "--", "--info"], text=True, capture_output=True, check=True)
            verification = subprocess.run([str(output), "--", "--verify"], text=True, capture_output=True, check=True)
            with tarfile.open(fileobj=io.BytesIO(output.read_bytes().split(b"\n", 9)[9]), mode="r:gz") as archive:
                release = json.load(archive.extractfile("targets/pico-jazzy/release-manifest.json"))
            self.assertEqual(release["modules"]["upperlimb"]["version"], "2.0.0-2")
            self.assertEqual(release["modules"]["upperlimb"]["sha256"], builder.file_sha256(run))
            self.assertEqual(release["modules"]["upperlimb"]["url"], run.as_uri())

        self.assertIn("Version: 2.0.0-1", info.stdout)
        self.assertIn("pico-jazzy", info.stdout)
        self.assertIn("payloads/pico-jazzy/common.deb", verification.stdout)

    def test_managed_services_are_stopped_until_total_install_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            version = directory / "version.json"
            urls = directory / "urls.json"
            version.write_text(json.dumps({"schema_version": 1, "version": "2.0.0-1", "output_name": "navi_one_stop_installer-2.0.0-1"}), encoding="utf-8")
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

    def test_vision_supervisor_uses_the_documented_isolated_dds_environment(self):
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-jazzy"]
        with tempfile.TemporaryDirectory() as temporary:
            stage = Path(temporary)
            startup, registrations, _, paths = builder.stage_supervisor_modules(
                stage, "orin-jazzy", target, [], False)
            launch = (stage / startup[0][1]).read_text()
            entrypoint = (stage / startup[0][2]).read_text()
            service = (stage / startup[0][3]).read_text()
            self.assertIn("vision.json", registrations[0][0])
            self.assertIn("supervisor-entrypoint.sh", service)
            self.assertIn("exec /usr/bin/supervisord", entrypoint)
            self.assertIn("192.168.217.100:19005", entrypoint)
            self.assertIn("source /opt/ros/jazzy/setup.bash", launch)
            self.assertIn("source /opt/naviai/venvs/vision/bin/activate", launch)
            self.assertIn("ROS_DOMAIN_ID=72", launch)
            self.assertIn("unset CYCLONEDDS_URI", launch)
            self.assertIn("zj-humanoid-orin-vision-supervisor.service", startup[0][0])

    def test_humble_vision_repairs_parent_log_permissions_before_dropping_user(self):
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        module = next(item for item in target["supervisor_modules"] if item["id"] == "vision")
        script = builder.supervisor_launch_script(module)
        self.assertLess(script.index("install -d -o naviai -g naviai -m 0750 /var/log/naviai/vision"),
                        script.index("exec runuser -u naviai"))
        # Reproduce the root-owned 0750 parent in a temporary directory, then
        # execute the actual prelude with local user/group names.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            import pwd
            import grp
            user = pwd.getpwuid(os.getuid()).pw_name
            group = grp.getgrgid(os.getgid()).gr_name
            parent = root / "var/log/naviai/vision"
            parent.mkdir(parents=True, mode=0o750)
            commands = "\n".join(module["prelude"]).replace(
                "-o naviai -g naviai", "-o {} -g {}".format(user, group)).replace("/var/", str(root / "var") + "/")
            subprocess.run(["bash", "-ec", commands], check=True)
            self.assertEqual(parent.stat().st_mode & 0o777, 0o750)
            self.assertEqual(parent.stat().st_uid, os.getuid())
            self.assertEqual((parent / "ros").stat().st_uid, os.getuid())
            (parent / "ros/test.log").write_text("ok")

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
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
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
                disabled_services=builder.supervisor_disabled_services("orin-humble", target),
            )

            robot_entrypoint = (stage / "targets/orin-humble/supervisor/robot/supervisor-entrypoint.sh").read_text(encoding="utf-8")
            audio_launch = (stage / "targets/orin-humble/supervisor/audio/launch.sh").read_text(encoding="utf-8")
            robot_unit = (stage / "targets/orin-humble/supervisor/robot/zj-humanoid-orin-robot-supervisor.service").read_text(encoding="utf-8")
            robot_registration = (stage / "targets/orin-humble/supervisor/modules/robot.json").read_text(encoding="utf-8")
            chassis_registration = (stage / "targets/orin-humble/supervisor/modules/chassis.json").read_text(encoding="utf-8")
            chassis_launch = (stage / "targets/orin-humble/supervisor/chassis/launch.sh").read_text(encoding="utf-8")
            livox_launch = (stage / "targets/orin-humble/supervisor/livox-lidar/launch.sh").read_text(encoding="utf-8")
            web_rviz_launch = (stage / "targets/orin-humble/supervisor/web-rviz/launch.sh").read_text(encoding="utf-8")
            manip_lingbot_launch = (stage / "targets/orin-humble/supervisor/manip-lingbot/launch.sh").read_text(encoding="utf-8")
            manip_lingbot_unit = (stage / "targets/orin-humble/supervisor/manip-lingbot/zj-humanoid-orin-manip-lingbot-supervisor.service").read_text(encoding="utf-8")
            agent_unit_exists = (stage / "targets/orin-humble/supervisor-agent/zj-humanoid-orin-supervisor-agent.service").is_file()
            agent_unit = (stage / "targets/orin-humble/supervisor-agent/zj-humanoid-orin-supervisor-agent.service").read_text()

        self.assertEqual(
            {item[0] for item in startup},
            {
                "zj-humanoid-orin-chassis-supervisor.service",
                "zj-humanoid-orin-manip-segmentation-supervisor.service",
                "zj-humanoid-orin-manip-sam6d-supervisor.service",
                "zj-humanoid-orin-manip-lingbot-supervisor.service",
                "zj-humanoid-orin-manip-hand-detect-supervisor.service",
                "zj-humanoid-orin-vanjee-lidar-supervisor.service",
                "zj-humanoid-orin-livox-lidar-supervisor.service",
                "zj-humanoid-orin-navigation-supervisor.service",
                "zj-humanoid-orin-naviai-nav2-supervisor.service",
                "zj-humanoid-orin-naviai-nav2-rawdata-supervisor.service",
                "zj-humanoid-orin-diagnosis-system-supervisor.service",
                "zj-humanoid-orin-web-rviz-supervisor.service",
                "zj-humanoid-orin-robot-supervisor.service",
                "zj-humanoid-orin-audio-supervisor.service",
                "zj-humanoid-orin-vision-supervisor.service",
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
        self.assertIn("http://192.168.217.100:19004/RPC2", chassis_registration)
        self.assertIn("ros2 launch chassis chassis.launch.py", chassis_launch)
        self.assertIn('detect_livox_model.py --lidar-ip "${LIVOX_LIDAR_IP:-192.168.217.17}" --timeout 4', livox_launch)
        self.assertIn("source /etc/naviai/Middleware.env", web_rviz_launch)
        self.assertIn("export ROS_LOG_DIR=/var/log/naviai/web-rviz/ros", web_rviz_launch)
        self.assertIn("exec /opt/zj_humanoid/lib/web_rviz_ros2/start_web_navigation.sh", web_rviz_launch)
        self.assertNotIn("exec source", web_rviz_launch)
        self.assertIn("source /etc/naviai/Middleware.env", manip_lingbot_launch)
        self.assertIn("exec /bin/bash /opt/naviai/manip/functions/bin/start-lingbot.sh", manip_lingbot_launch)
        self.assertNotIn("env ROS_DOMAIN_ID=72", manip_lingbot_launch)
        self.assertIn("After=network-online.target zj-humanoid-orin-supervisor-agent.service navi-sensor-host.service", manip_lingbot_unit)
        self.assertIn("/etc/naviai/supervisor-agent/modules.d/robot.json", script)
        self.assertNotIn("configure_sensor_rpc.py", script)
        self.assertIn('"navi-sensor-host.service"', script)
        self.assertIn("Disabling vendor service replaced by Supervisor: $unit", script)
        self.assertIn("zj-humanoid-chassis.service", script)
        self.assertNotIn('[[ -f "/etc/systemd/system/$unit" ]]', script)
        self.assertIn("systemctl restart zj-humanoid-orin-supervisor-agent.service", script)
        self.assertTrue(agent_unit_exists)
        self.assertIn("Conflicts=navi-orin-supervisor-agent.service navi-supervisor-agent.service", agent_unit)
        self.assertIn("ExecStartPost=/usr/bin/python3", agent_unit)
        self.assertIn("--wait-ready", agent_unit)
        self.assertIn("systemctl disable --now navi-supervisor-agent.service", script)
        self.assertLess(script.index("systemctl disable --now navi-supervisor-agent.service"),
                        script.index("systemctl restart zj-humanoid-orin-supervisor-agent.service"))
        self.assertIn("/etc/naviai/supervisor-agent/modules.d/vision.json", script)
        self.assertIn("configure_native_rpc.py /etc/naviai/navi-sensor-host-supervisor.conf", script)
        self.assertNotIn("/bin/bash -lc", audio_launch)
        self.assertIn("exec ros2 launch navi_audio_pkg audio_bringup.launch.py", audio_launch)

    def test_lingbot_waits_for_sensor_and_restarts_while_camera_initializes(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        lingbot = next(module for module in target["supervisor_modules"] if module["id"] == "manip-lingbot")
        self.assertEqual(lingbot["after_services"], ["navi-sensor-host.service"])
        self.assertEqual(lingbot["startup_priority"], 110)
        self.assertEqual(lingbot["autorestart"], "true")

    def test_replaced_vendor_services_are_disabled_again_after_run_installation(self) -> None:
        script = builder.target_install(
            "orin-humble", "payloads/orin-humble/system-config", "", None, [],
            [("payloads/orin-humble/run-vision.run", [])], [],
            disabled_services=["navi-vision.service", "navi-vision-supervisor.service"],
        )
        run_index = script.index('/bin/bash "$root/payloads/orin-humble/run-vision.run"')
        self.assertGreater(script.rfind("disable_replaced_services"), run_index)
        self.assertIn("navi-vision-supervisor.service", script)

    def test_navigation_chassis_services_are_managed_by_supervisord(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["orin-humble"]
        chassis = next(module for module in target["supervisor_modules"] if module["id"] == "chassis")
        self.assertEqual(chassis["mode"], "managed")
        self.assertEqual(chassis["port"], 19004)
        self.assertEqual(chassis["disable_services"], ["zj-humanoid-chassis.service"])
        vision = next(module for module in target["supervisor_modules"] if module["id"] == "vision")
        self.assertEqual(vision["disable_services"], ["navi-vision.service", "navi-vision-supervisor.service"])
        services = builder.supervisor_systemd_services("orin-humble", target)
        self.assertEqual(services, [])
        disabled = builder.supervisor_disabled_services("orin-humble", target)
        self.assertIn("zj-humanoid-chassis.service", disabled)
        self.assertIn("zj-humanoid-navigation.service", disabled)

    def test_nav2_background_package_is_waited_before_supervisor_services_start(self) -> None:
        script = builder.target_install(
            "orin-humble", "system-config", "", None,
            [("payloads/nav2.deb", [], [], None, "navigation", ["zj-humanoid-ros-humble-naviai-nav2-bringup"])],
            [], ["zj-humanoid-orin-naviai-nav2-supervisor.service"],
        )
        self.assertIn("wait_for_debian_package()", script)
        self.assertIn("wait_for_debian_package zj-humanoid-ros-humble-naviai-nav2-bringup", script)
        self.assertLess(script.index("wait_for_debian_package zj-humanoid-ros-humble-naviai-nav2-bringup"),
                        script.index("systemctl restart \"$unit\""))

    def test_native_services_restart_once_and_agent_failure_keeps_cleanup_active(self) -> None:
        for agent_status in (0, 7):
            with self.subTest(agent_status=agent_status), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                target = root / "targets/test"
                target.mkdir(parents=True)
                bin_dir = root / "bin"
                bin_dir.mkdir()
                log = root / "calls"
                stubs = {
                    "sha256sum": "exit 0\n",
                    "install": "exit 0\n",
                    "python3": "exit 0\n",
                    "systemctl": (
                        'echo "$*" >> "$CALL_LOG"\n'
                        'if [[ "$*" == "restart test-agent.service" ]]; then exit "$AGENT_STATUS"; fi\n'
                        'exit 0\n'
                    ),
                }
                for name, body in stubs.items():
                    executable = bin_dir / name
                    executable.write_text("#!/bin/bash\n" + body)
                    executable.chmod(0o755)
                (target / "install-system-config.sh").write_text("exit 0\n")
                script = target / "install.sh"
                script.write_text(builder.target_install(
                    "test", "payloads/system-config", "", None, [], [], ["native.service"],
                    post_install=["systemctl restart native.service", "systemctl restart sensor.service"],
                    agent_payload={"base": "agent", "destination": str(root / "agent"), "service": "test-agent.service"},
                ))
                result = subprocess.run(
                    ["bash", str(script), "WA1"], capture_output=True, text=True,
                    env={**os.environ, "PATH": str(bin_dir) + ":" + os.environ["PATH"],
                         "CALL_LOG": str(log), "AGENT_STATUS": str(agent_status)},
                )
                self.assertEqual(result.returncode, agent_status, result.stderr)
                calls = log.read_text().splitlines()
                for service in ("native.service", "sensor.service"):
                    self.assertEqual(calls.count("restart " + service), 1)
                    self.assertEqual(calls.count("stop " + service), 1 if agent_status == 0 else 2)
                if agent_status:
                    self.assertIn("managed services are being kept stopped", result.stderr)

    def test_pico_native_supervisor_modules_are_registered_without_config_injection(self) -> None:
        config = builder.load_delivery(ROOT / "one_stop/package-urls.json")
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

            display_launch = (stage / "targets/pico-humble/supervisor/display/launch.sh").read_text()
            self.assertLess(display_launch.index("/opt/ros/humble/setup.bash"),
                            display_launch.index("/opt/navi_display/ros/setup.bash"))
            self.assertIn("exec ros2 launch media_play media_play.launch.py", display_launch)

        self.assertEqual([item[0] for item in startup], ["zj-humanoid-pico-display-supervisor.service"])
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

    def test_pico_startup_priority_waits_for_each_service_to_be_active(self) -> None:
        target = builder.load_delivery(ROOT / "one_stop/package-urls.json")["targets"]["pico-humble"]
        priorities = builder.supervisor_service_priorities("pico-humble", target)
        script = builder.target_install(
            "pico-humble", "payloads/pico-humble/system-config", "", None, [], [],
            target["managed_services"] + ["zj-humanoid-pico-display-supervisor.service"], service_priorities=priorities,
        )
        robot = "navi-pico-robot-supervisor.service"
        upperlimb = "navi-pico-upperlimb.service"
        self.assertEqual(priorities[robot], 10)
        self.assertEqual(priorities[upperlimb], 20)
        self.assertEqual(priorities["zj-humanoid-pico-display-supervisor.service"], 1)
        self.assertLess(script.index('"zj-humanoid-pico-display-supervisor.service"'),
                        script.index('"{}"'.format(robot)))
        self.assertLess(script.index('"{}"'.format(robot)),
                        script.index('"{}"'.format(upperlimb)))
        self.assertIn('systemctl is-active --quiet "$unit"', script)


if __name__ == "__main__":
    unittest.main()
