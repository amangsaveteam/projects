#!/bin/bash
set -eo pipefail

# The Supervisor prelude loads audio.env and the shared ROS/venv environment.
case "${AUDIO_MODE:-full}" in
    speaker-only)
        [[ -n "${SPK_DEVICE:-}" ]] || {
            echo 'ERROR: speaker-only mode requires SPK_DEVICE in /etc/navi-audio/audio.env' >&2
            exit 1
        }
        exec ros2 launch /usr/lib/naviai/audio/audio_speaker.launch.py "spk_device:=$SPK_DEVICE"
        ;;
    full)
        exec ros2 launch navi_audio_pkg audio_bringup.launch.py "$@"
        ;;
    *)
        echo "ERROR: unsupported AUDIO_MODE=${AUDIO_MODE}" >&2
        exit 1
        ;;
esac
