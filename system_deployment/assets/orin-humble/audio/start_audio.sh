#!/bin/bash
set -eo pipefail

# USB/ALSA devices can appear after the service starts. Wait until both
# playback and capture devices are visible before creating ROS audio nodes.
alsa_ready=0
for attempt in $(seq 1 60); do
    if aplay -l >/dev/null 2>&1 && arecord -l >/dev/null 2>&1; then
        alsa_ready=1
        break
    fi
    sleep 1
done
if [[ "$alsa_ready" != 1 ]]; then
    echo "ERROR: ALSA playback/capture devices did not become ready within 60 seconds" >&2
    exit 1
fi

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
