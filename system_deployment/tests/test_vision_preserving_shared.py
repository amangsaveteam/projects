import importlib.util
from pathlib import Path
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('vision_adapter', Path(__file__).resolve().parents[1] / 'one_stop/install_vision_preserving_shared.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class VisionSharedPackageTest(unittest.TestCase):
    def test_installed_shared_package_is_excluded_from_all_transaction_lists(self):
        for status in ('installed', 'config-files', ''):
            with self.subTest(status=status):
                script = 'set -eu\nold_release_id= release_id=x old_venv= old_runtime=\ndpkg-query() { printf "%s" "' + status + '"; }\n'
                script += adapter.DECLARATION + '\n'
                script += 'if false; then\n  :\n' + adapter.SAME_RELEASE_PREVIOUS + '\n  :\nfi\n'
                script += 'readonly VISION_PACKAGES=(common "${ROS_PACKAGES[@]}")\n'
                script += 'printf "transaction:%s\\n" "${VISION_PACKAGES[@]}"\n'
                script += 'printf "install:%s\\n" "${ROS_PACKAGES[@]}"\n'
                script += 'printf "restore:%s\\n" common "${ROS_PACKAGES[@]}"\n'
                result = subprocess.run(['bash', '-c', adapter.adapt_installer(script)], check=True, text=True, capture_output=True)
                for operation in ('transaction', 'install', 'restore'):
                    self.assertEqual(operation + ':ros-humble-upperlimb-msgs' in result.stdout, status != 'installed')
                    self.assertIn(operation + ':ros-humble-navi-vision-pkg', result.stdout)

    def test_same_release_does_not_publish_itself_as_previous(self):
        adapted = adapter.adapt_installer(adapter.DECLARATION + '\n' + adapter.SAME_RELEASE_PREVIOUS)
        self.assertIn('"${old_release_id}" != "${release_id}"', adapted)

    def test_unknown_vendor_format_fails_before_execution(self):
        for text in ('', adapter.DECLARATION + '\n' + adapter.DECLARATION):
            with self.assertRaises(RuntimeError):
                adapter.adapt_installer(text)
        with self.assertRaises(RuntimeError):
            adapter.adapt_installer(adapter.DECLARATION)
