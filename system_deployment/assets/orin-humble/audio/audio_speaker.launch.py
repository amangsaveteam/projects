"""Playback and TTS only; no capture, ASR, wake-word or voiceprint nodes."""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    directory = os.path.join(get_package_share_directory("navi_audio_pkg"), "launch")
    speaker = LaunchConfiguration("spk_device")
    venv = LaunchConfiguration("venv_path")
    actions = [
        DeclareLaunchArgument("spk_device", default_value=os.environ.get("SPK_DEVICE", "")),
        DeclareLaunchArgument("venv_path", default_value="/opt/naviai/venvs/audio"),
    ]
    for filename, arguments in (
        ("audio_play.launch.py", {"spk_device": speaker}),
        ("sound_play.launch.py", {"spk_device": speaker}),
        ("tts_stream.launch.py", {"venv_path": venv}),
    ):
        actions.append(IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(directory, filename)),
            launch_arguments=arguments.items(),
        ))
    return LaunchDescription(actions)
