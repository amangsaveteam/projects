import importlib.util
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audio_install', ROOT / 'one_stop/install_run_without_final_exec.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class SpeakerModeTest(unittest.TestCase):
    def test_speaker_mode_keeps_installation_but_skips_microphone(self):
        script = '''set -euo pipefail
die() { echo "$*" >&2; exit 1; }
load_audio_environment() { :; }
echo packages-installed
load_audio_environment
echo microphone-required
exit 9
'''
        for mode, speaker, expected in [('speaker-only', 'plughw:CARD=Device,DEV=0', 0),
                                         ('speaker-only', '', 1), ('full', '', 9)]:
            result = subprocess.run(['bash', '-c', helper.allow_speaker_only_install(script)],
                                    env={**os.environ, 'AUDIO_MODE': mode, 'SPK_DEVICE': speaker,
                                         'ENV_FILE': '/etc/navi-audio/audio.env'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertIn('packages-installed', result.stdout)
            self.assertEqual('microphone-required' in result.stdout, mode == 'full')

    def test_runtime_selects_playback_graph_or_preserves_full_arguments(self):
        script = (ROOT / 'one_stop/audio/start_audio.sh').read_text()
        # Shadow ros2 through a shell function, including the final exec.
        script = script.replace('exec ros2 ', 'ros2 ')
        stub = 'ros2() { printf "%s\\n" "$@"; }\n'
        for mode, expected in [('speaker-only', 'audio_speaker.launch.py'),
                               ('full', 'audio_bringup.launch.py')]:
            result = subprocess.run(['bash', '-c', stub + script, 'test', 'intent_router_mode:=bt'],
                                    env={**os.environ, 'AUDIO_MODE': mode, 'SPK_DEVICE': 'plughw:CARD=Device,DEV=0'},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)
            self.assertEqual('intent_router_mode:=bt' in result.stdout, mode == 'full')


if __name__ == '__main__':
    unittest.main()
