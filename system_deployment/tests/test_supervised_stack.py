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
    "build_supervised_stack", ROOT / "supervised_stack/build_supervised_stack.py"
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


class SupervisedStackTest(unittest.TestCase):
    def test_generates_managed_module_and_fetch_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest = directory / "stack.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "name": "test stack",
                "output_name": "test-stack-1.0.0-arm64",
                "target": {
                    "platform": "ORIN", "os_version": "22.04", "architecture": "arm64",
                    "internal_ip": "192.168.217.100", "middleware_env": "/etc/naviai/Middleware.env",
                    "agent_service": "navi-orin-supervisor-agent", "agent_port": 9080,
                },
                "modules": [{
                    "id": "demo", "description": "Demo process",
                    "artifact": {"type": "run", "url": "https://example.test/demo.run", "sha256": "a" * 64, "delivery": "fetch"},
                    "install": {"arguments": ["--install"]},
                    "supervisor": {
                        "mode": "managed", "port": 19010, "working_directory": "/var/lib/demo",
                        "source_files": ["/etc/naviai/Middleware.env"], "environment": {"ROS_LOG_DIR": "/var/log/demo"},
                        "command": "ros2 launch demo bringup.launch.py",
                    },
                }],
            }), encoding="utf-8")
            output = builder.build(manifest, directory / "out")
            payload = output.read_bytes().split(b"\n", 9)[9]
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                names = archive.getnames()
                install = archive.extractfile("install.sh").read().decode("utf-8")
                launch = archive.extractfile("generated/demo/launch.sh").read().decode("utf-8")
                entrypoint = archive.extractfile("generated/demo/supervisor-entrypoint.sh").read().decode("utf-8")
                module_config = archive.extractfile("generated/modules/demo.json").read().decode("utf-8")

        self.assertNotIn("payloads/demo.run", names)
        self.assertIn("/var/cache/naviai/supervised-stack/demo.run", install)
        self.assertIn("sha256sum -c", install)
        self.assertIn('if [[ "$action" == --pretest ]]', install)
        self.assertIn('if [[ "$action" == --uninstall ]]', install)
        self.assertIn("disable --now navi-orin-demo-supervisor.service", install)
        self.assertIn("Installed payload packages are retained", install)
        self.assertIn("source /etc/naviai/Middleware.env", launch)
        self.assertIn("exec /bin/bash -lc", launch)
        self.assertIn("port=192.168.217.100:19010", entrypoint)
        self.assertIn("supervisor.rpcinterface_factory", entrypoint)
        self.assertEqual("http://192.168.217.100:19010/RPC2", json.loads(module_config)["modules"]["demo"]["endpoint"])

    def test_fetch_requires_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text(json.dumps({
                "schema_version": 1, "name": "bad", "output_name": "bad",
                "target": {"platform": "ORIN", "os_version": "22.04", "architecture": "arm64", "internal_ip": "192.168.217.100", "middleware_env": "/etc/naviai/Middleware.env", "agent_service": "agent", "agent_port": 9080},
                "modules": [{"id": "bad", "description": "Bad", "artifact": {"type": "deb", "url": "https://example.test/a.deb", "delivery": "fetch"}, "supervisor": {"mode": "external", "port": 19001}}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(builder.StackError, "requires artifact.sha256"):
                builder.load_manifest(path)

    def test_embedded_remote_module_also_requires_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid-embed.json"
            path.write_text(json.dumps({
                "schema_version": 1, "name": "bad", "output_name": "bad",
                "target": {"platform": "ORIN", "os_version": "22.04", "architecture": "arm64", "internal_ip": "192.168.217.100", "middleware_env": "/etc/naviai/Middleware.env", "agent_service": "agent", "agent_port": 9080},
                "modules": [{"id": "bad", "description": "Bad", "artifact": {"type": "deb", "url": "https://example.test/a.deb", "delivery": "embed"}, "supervisor": {"mode": "external", "port": 19001}}],
            }), encoding="utf-8")
            with self.assertRaisesRegex(builder.StackError, "requires artifact.sha256"):
                builder.load_manifest(path)

    def test_remote_artifact_uses_matching_workspace_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            cached = workspace / "dist" / "demo.run"
            cached.parent.mkdir()
            cached.write_text("cached artifact", encoding="utf-8")
            original_root = builder.WORKSPACE_ROOT
            builder.WORKSPACE_ROOT = workspace
            try:
                self.assertEqual(cached, builder.cached_source("https://example.test/releases/demo.run", builder.sha256(cached)))
                self.assertIsNone(builder.cached_source("https://example.test/releases/demo.run", "0" * 64))
            finally:
                builder.WORKSPACE_ROOT = original_root

    def test_pico_manifest_uses_pico_agent_paths(self) -> None:
        source_manifest = ROOT / "supervised_stack/configs/pico-humble.json"
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manifest_data = json.loads(source_manifest.read_text(encoding="utf-8"))
            for module in manifest_data["modules"]:
                module["artifact"].pop("sha256", None)
                module["artifact"]["url"] = "local://output/{}.run".format(module["id"])
            manifest = directory / "pico-humble.json"
            manifest.write_text(json.dumps(manifest_data), encoding="utf-8")
            agent = directory / "agent.run"
            legged = directory / "legged"
            upperlimb = directory / "upperlimb"
            agent.write_text("#!/bin/sh\n", encoding="utf-8")
            legged.mkdir()
            upperlimb.mkdir()
            (legged / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (upperlimb / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            output = builder.build(manifest, directory / "out", {"agent": agent, "legged": legged, "upperlimb": upperlimb})
            payload = output.read_bytes().split(b"\n", 9)[9]
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
                names = archive.getnames()
                install = archive.extractfile("install.sh").read().decode("utf-8")
                legged_launch = archive.extractfile("generated/legged/launch.sh").read().decode("utf-8")
                upperlimb_service = archive.extractfile("generated/upperlimb/navi-pico-upperlimb.service").read().decode("utf-8")
                middleware_env = archive.extractfile("generated/environment/middleware_env").read().decode("utf-8")

        self.assertIn("/etc/nav01/supervisor-agent/modules.d", install)
        self.assertIn("generated/environment/middleware_env", names)
        self.assertIn("generated/environment/device_env", names)
        self.assertIn("navi-pico-upperlimb.service", install)
        self.assertIn("payloads/upperlimb.bundle/install.sh", names)
        self.assertIn("payloads/legged.bundle/install.sh", names)
        self.assertIn("export ROS_DOMAIN_ID=72", legged_launch)
        self.assertIn("export ROS_LOCALHOST_ONLY=0", legged_launch)
        self.assertIn("export ROS_DOMAIN_ID=72", middleware_env)
        self.assertIn("export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp", middleware_env)
        self.assertIn("export ROS_LOCALHOST_ONLY=0", middleware_env)
        self.assertIn("export CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml", middleware_env)
        self.assertIn("export ROS2CLI_DISABLE_DAEMON=1", middleware_env)
        self.assertIn("disable --now navi-pico-legged-supervisor.service", install)
        self.assertIn("navi-pico-upperlimb.service", install)
        self.assertIn("After=network-online.target navi-pico-supervisor-agent.service navi-pico-legged-supervisor.service", upperlimb_service)
        self.assertIn("PartOf=navi-pico-legged-supervisor.service", upperlimb_service)
        self.assertLess(install.index("module legged requires --robot-type"), install.index("INSTALL AGENT"))


if __name__ == "__main__":
    unittest.main()
