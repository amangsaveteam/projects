import json
import tempfile
import unittest
from pathlib import Path
from test_build_offline_common_bundle import common_builder, ROOT


class OrbbecOfflineTest(unittest.TestCase):
    def test_dedicated_carrier_does_not_overwrite_common_installer(self):
        config = json.loads((ROOT / 'common/configs/orin-orbbec-humble.json').read_text())
        self.assertEqual(config['target']['architecture'], 'arm64')
        packages = common_builder.parse_manifest(ROOT / config['manifest'])
        self.assertEqual(dict(packages), {
            'ros-humble-orbbec-camera': '2.9.3',
            'ros-humble-orbbec-description': '2.9.3',
            'ros-humble-orbbec-camera-msgs': '2.9.3',
        })
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            common_builder.write_installer(staging, config['package_name'], config['installer_aliases'])
            common_builder.verify_common_carrier(staging, config['installer_aliases'][0])
            self.assertFalse((staging / 'usr/sbin/install_common_deps.sh').exists())
            script = (staging / 'usr/sbin/install_orbbec_deps.sh').read_text()
            self.assertIn('--no-download', script)
            self.assertIn('file:', script)
