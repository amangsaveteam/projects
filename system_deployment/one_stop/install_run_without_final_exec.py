#!/usr/bin/env python3
"""Install an Audio vendor run package but leave its final ROS launch to Supervisor."""

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


FINAL_LAUNCH = 'exec ros2 launch navi_audio_pkg audio_bringup.launch.py "${AUDIO_LAUNCH_ARGS[@]}"'
REPLACEMENT = 'echo "Audio installed; startup is managed by navi-orin-audio-supervisor.service"'


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
        text = installer.read_text(encoding="utf-8")
        if text.count(FINAL_LAUNCH) != 1:
            raise RuntimeError("cannot identify the vendor Audio final launch")
        installer.write_text(text.replace(FINAL_LAUNCH, REPLACEMENT), encoding="utf-8")
        subprocess.run(["/bin/bash", str(installer), *args.arguments], cwd=payload, check=True)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
