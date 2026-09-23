# Audio playback-only mode

Set these values in the device's existing `/etc/navi-audio/audio.env`, keeping
its other settings and credentials:

```sh
AUDIO_MODE=speaker-only
SPK_DEVICE=plughw:CARD=Device,DEV=0
```

Use the actual ALSA playback card name from `aplay -l`. `plughw` enables format
conversion when the speaker hardware requires stereo but the source is mono.

The one-stop Audio installer still installs and verifies the vendor dependencies,
function packages and runtime assets. After loading the persistent environment,
speaker-only mode skips microphone detection and capture-specific initialization.
Supervisor starts `audio_play`, `sound_play` and `tts_stream` through
`audio_speaker.launch.py`. Capture, ASR, wake-word, voiceprint and dialogue nodes
are not started. TTS still requires its configured provider credentials and
network access.

`AUDIO_MODE=full` (the default) preserves the vendor's complete Audio graph and
microphone checks. Re-run installation when switching back to full mode so its
capture configuration and license checks are completed.
