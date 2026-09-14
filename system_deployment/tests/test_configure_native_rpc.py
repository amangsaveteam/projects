import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_rpc', Path(__file__).resolve().parents[1] / 'packages/supervisor-agent/scripts/configure_native_rpc.py')
rpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rpc)


class NativeRpcTest(unittest.TestCase):
    def test_reinstall_preserves_native_programs_and_updates_credential(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, secret = root / 'supervisor.conf', root / 'password'
            original = '[rpcinterface:supervisor]\nsupervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface\n[program:camera]\ncommand=camera\n'
            config.write_text(original)
            secret.write_text('a' * 64)
            rpc.configure(config, secret, '192.168.217.100:19001')
            first = config.read_text()
            rpc.configure(config, secret, '192.168.217.100:19001')
            self.assertEqual(first.strip(), config.read_text().strip())
            secret.write_text('1\n')
            rpc.configure(config, secret, '192.168.217.100:19001')
            self.assertEqual(config.read_text().count('[inet_http_server]'), 1)
            self.assertIn(original.strip(), config.read_text())
            self.assertNotIn('a' * 64, config.read_text())
            self.assertIn('password=1\n', config.read_text())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.with_name('supervisor.conf.before-one-stop-rpc').read_text(), original)
