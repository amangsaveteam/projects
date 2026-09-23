import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('isolated', Path(__file__).resolve().parents[1] / 'common/isolated_humble_apt.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class IsolatedAptTest(unittest.TestCase):
    def test_arm64_sources_and_state_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(module.urllib.request, 'urlopen', return_value=io.BytesIO(b'key')), patch.object(module.subprocess, 'run') as run, patch.object(module.Path, 'is_file', return_value=True), patch.dict(os.environ, {}, clear=True):
                options = module.prepare(root)
            sources = (root / 'sources.list').read_text()
            self.assertIn('arch=arm64 signed-by=', sources)
            self.assertIn('jammy-security', sources)
            self.assertNotIn('trusted=yes', sources)
            self.assertIn('APT::Architecture=arm64', options)
            self.assertIn('Dir::Etc::sourceparts=' + str(root / 'empty'), options)
            self.assertIn('Dir::State::lists=' + str(root / 'lists'), options)
            self.assertEqual(run.call_args.args[0][-1], 'update')
            self.assertIn('#clear APT::Update::Post-Invoke;', (root / 'apt.conf').read_text())
