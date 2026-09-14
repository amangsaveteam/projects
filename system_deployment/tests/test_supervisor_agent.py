#!/usr/bin/env python3
import importlib.util
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "navi_supervisor_agent",
    ROOT / "packages/supervisor-agent/resources/navi_supervisor_agent.py",
)
agent_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(agent_module)


class SupervisorAgentTest(unittest.TestCase):
    def test_installers_replace_legacy_password_with_fixed_password(self):
        for filename in ("initialize_agent_secrets.py", "initialize_orin_agent_secrets.py"):
            with self.subTest(initializer=filename), tempfile.TemporaryDirectory() as temporary:
                spec = importlib.util.spec_from_file_location(
                    "initializer", ROOT / "packages/supervisor-agent/scripts" / filename
                )
                initializer = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(initializer)
                path = Path(temporary) / "password"
                initializer.ensure_secret(path)
                self.assertEqual(path.read_text(), "1\n")
                path.write_text("a" * 64 + "\n")
                path.chmod(0o644)
                initializer.ensure_secret(path)
                initializer.ensure_secret(path)
                self.assertEqual(path.read_text(), "1\n")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unknown_module_log_returns_json_404(self):
        handler = object.__new__(agent_module.Handler)
        handler.path = '/api/v1/modules/missing/processes/example/log'
        handler.agent = MagicMock()
        handler.agent.process_log.side_effect = KeyError('unknown module')
        handler.write_json = MagicMock()
        handler.do_GET()
        self.assertEqual(handler.write_json.call_args.args[0], 404)

    def test_readiness_retries_connection_refused_and_checks_device(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"device":"orin"}'
        with patch.object(agent_module.urllib.request, "build_opener") as factory, \
             patch.object(agent_module.time, "sleep"):
            factory.return_value.open.side_effect = [ConnectionRefusedError(), response]
            agent_module.wait_ready({"device": "orin"})
            self.assertEqual(factory.return_value.open.call_count, 2)
            self.assertEqual(factory.return_value.open.call_args.args[0], "http://127.0.0.1:9080/api/v1/health")

    def test_readiness_rejects_wrong_agent(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"device":"pico"}'
        with patch.object(agent_module.urllib.request, "build_opener") as factory:
            factory.return_value.open.return_value = response
            with self.assertRaisesRegex(RuntimeError, "did not become ready"):
                agent_module.wait_ready({"device": "orin"}, timeout=0)

    def test_agent_http_has_no_token_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rpc_password = directory / "rpc.password"
            agent_module.ensure_secret(rpc_password)
            config = {
                "device": "pico",
                "listen": "127.0.0.1",
                "port": 0,
                "rpc_password_file": str(rpc_password),
                "modules": {},
            }
            agent_module.Handler.agent = agent_module.Agent(config)
            self.assertFalse(hasattr(agent_module.Handler.agent, "token"))

    def test_dashboard_includes_process_controls_and_log_tail(self):
        for label in ("启动", "停止", "重启", "日志尾部", "自动刷新", "PID", "退出码"):
            self.assertIn(label, agent_module.INDEX_HTML)
        self.assertIn("button.closest('.process')", agent_module.INDEX_HTML)
        self.assertIn("version!==entry.version", agent_module.INDEX_HTML)

    def test_secret_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "secret"
            path.write_text("a" * 64 + "\n")
            first = agent_module.ensure_secret(path)
            second = agent_module.ensure_secret(path)
            self.assertEqual(first, "1")
            self.assertEqual(agent_module.read_secret(path), "1")
            self.assertEqual(first, second)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_remote_agent_modules_are_namespaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            rpc_password = directory / "rpc.password"
            agent_module.ensure_secret(rpc_password)
            agent = agent_module.Agent(
                {
                    "device": "orin",
                    "rpc_password_file": str(rpc_password),
                    "modules": {},
                    "remote_agents": {
                        "pico": {"endpoint": "http://192.168.217.66:9080"}
                    },
                },
            )
            calls = []

            def remote_request(specification, method, path):
                calls.append((method, path))
                if method == "GET" and path == "/api/v1/modules":
                    return {
                        "device": "pico",
                        "modules": [{"id": "upperlimb", "name": "upperlimb", "reachable": True, "processes": []}],
                    }
                return {"changed": True}

            agent.remote_request = remote_request
            status = agent.all_status()
            self.assertEqual(status[0]["id"], "pico/upperlimb")
            self.assertEqual(status[0]["device"], "pico")
            self.assertEqual(agent.process_action("pico/upperlimb", "upperlimb_interface", "restart"), {"changed": True})
            self.assertEqual(calls[-1], ("POST", "/api/v1/modules/upperlimb/processes/upperlimb_interface/restart"))

    def test_module_overlay_is_merged_with_base_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            overlays = directory / "modules.d"
            overlays.mkdir()
            base = directory / "modules.json"
            base.write_text(
                json.dumps(
                    {
                        "rpc_password_file": "/tmp/rpc.password",
                        "modules": {},
                        "module_config_directory": str(overlays),
                    }
                ),
                encoding="utf-8",
            )
            (overlays / "robot.json").write_text(
                json.dumps({"modules": {"robot": {"endpoint": "http://192.168.217.100:19002/RPC2"}}}),
                encoding="utf-8",
            )
            config = agent_module.load_config(base)
            self.assertEqual(config["modules"]["robot"]["endpoint"], "http://192.168.217.100:19002/RPC2")

    def test_rpc_transport_separates_credentials_from_host(self):
        transport = agent_module.TimeoutTransport(3)
        connection = transport.make_connection("agent:0123456789abcdef@192.168.217.66:19003")
        self.assertEqual(connection.host, "192.168.217.66")
        self.assertEqual(connection.port, 19003)
        self.assertTrue(any(name == "Authorization" and value.startswith("Basic ") for name, value in transport._extra_headers))

    def test_unreachable_module_reports_its_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            password = Path(temporary) / "rpc.password"
            agent_module.ensure_secret(password)
            agent = agent_module.Agent({
                "rpc_password_file": str(password),
                "modules": {"chassis": {"endpoint": "http://192.168.217.100:19004/RPC2"}},
            })
            agent.proxy = lambda module: (_ for _ in ()).throw(OSError("Connection refused"))
            status = agent.module_status("chassis")
        self.assertFalse(status["reachable"])
        self.assertIn("192.168.217.100:19004", status["error"])


if __name__ == "__main__":
    unittest.main()
