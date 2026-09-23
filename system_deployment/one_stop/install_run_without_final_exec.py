#!/usr/bin/env python3
"""Install an Audio vendor run package but leave its final ROS launch to Supervisor."""

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


REPLACEMENT = 'echo "Audio installed; startup is managed by zj-humanoid-orin-audio-supervisor.service"'
# Audio release bundles have changed their final command more than once: some
# use ``exec`` or ``exec_as_runtime_user``, some expand different argument variables. Match the concrete
# Audio package/launch-file pair instead of an entire shell line, then replace
# only that final launch statement.  This preserves every dependency and
# runtime-layout step in the vendor installer.
FINAL_LAUNCH = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?:(?:exec|exec_as_runtime_user)[ \t]+)?ros2[ \t]+launch[ \t]+"
    r"navi_audio_pkg[ \t]+audio_bringup\.launch\.py\b[^\r\n]*$"
)


def allow_speaker_only_install(installer: str) -> str:
    """Finish after package/layout verification when capture is explicitly disabled."""
    anchor = "\nload_audio_environment\n"
    if installer.count(anchor) != 1:
        # Other vendor versions still use the existing full-install behavior.
        return installer
    branch = '''
if [[ "${AUDIO_MODE:-full}" == speaker-only ]]; then
    [[ -n "${SPK_DEVICE:-}" ]] || die "speaker-only mode requires SPK_DEVICE in ${ENV_FILE}"
    echo "Audio speaker-only installation completed; playback and TTS are managed by Supervisor"
    exit 0
fi
'''
    return installer.replace(anchor, anchor + branch, 1)


def suppress_final_audio_launch(installer: str) -> str:
    matches = list(FINAL_LAUNCH.finditer(installer))
    if len(matches) != 1:
        raise RuntimeError(
            "cannot identify the vendor Audio final launch: expected one "
            "navi_audio_pkg audio_bringup.launch.py command, found {}".format(len(matches))
        )
    return FINAL_LAUNCH.sub(lambda match: match.group("indent") + REPLACEMENT, installer, count=1)


def run_with_audio_launch_guard(installer: Path, arguments: list[str], payload: Path) -> None:
    """Run an unfamiliar vendor installer while suppressing its Audio launch.

    The fallback keeps the vendor script's dependency installation intact.  A
    Bash function shadows both direct ``ros2`` and ``exec ros2`` launches for
    the one Audio launch pair; every other ``ros2`` invocation and ``exec``
    keeps normal Bash behaviour.
    """
    guard = r'''
is_audio_launch() {
    [[ "${1:-}" == launch && "${2:-}" == navi_audio_pkg && "${3:-}" == audio_bringup.launch.py ]]
}
ros2() {
    if is_audio_launch "$@"; then
        echo "Audio installed; startup is managed by zj-humanoid-orin-audio-supervisor.service"
        return 0
    fi
    command ros2 "$@"
}
exec() {
    if [[ "${1:-}" == ros2 ]] && is_audio_launch "${@:2}"; then
        echo "Audio installed; startup is managed by zj-humanoid-orin-audio-supervisor.service"
        return 0
    fi
    builtin exec "$@"
}
source "$1" "${@:2}"
'''
    subprocess.run(
        ["/bin/bash", "-c", guard, "navi-audio-launch-guard", str(installer), *arguments],
        cwd=payload,
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    workspace = Path(tempfile.mkdtemp(prefix="navi-audio-install-"))
    try:
        payload = workspace / "payload"
        subprocess.run(["/bin/bash", str(args.run), "--noexec", "--target", str(payload)], check=True)
        installer = payload / "install.sh"
        text = allow_speaker_only_install(installer.read_text(encoding="utf-8"))
        installer.write_text(text, encoding="utf-8")
        try:
            installer.write_text(suppress_final_audio_launch(text), encoding="utf-8")
        except RuntimeError as error:
            print("WARN: {}; using the Audio launch guard.".format(error))
            run_with_audio_launch_guard(installer, args.arguments, payload)
        else:
            subprocess.run(["/bin/bash", str(installer), *args.arguments], cwd=payload, check=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
