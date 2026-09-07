#!/usr/bin/env python3
"""Run the vendor Audio installer, suppressing only its final foreground launch."""

import argparse
import datetime
import shutil
import subprocess
import tempfile
from pathlib import Path


FINAL_LAUNCH = 'exec ros2 launch navi_audio_pkg audio_bringup.launch.py "${AUDIO_LAUNCH_ARGS[@]}"'
REPLACEMENT = 'echo "Audio installed; startup is managed by navi-orin-audio-supervisor.service"\nexit 0'
INITIALIZER = '"${SUDO[@]}" "/usr/lib/${AUDIO_COMMON_PACKAGE}/install_audio_deps.sh"'
# `orin-audio-common-deb` deliberately pins websockets 10.4 (its runtime
# verifier requires it), while two bundled SDK wheels incorrectly declare
# websockets>=14.  The vendor initializer already accepts those two exact
# pip-check lines.  pwdlib is a system-site package, not an Audio dependency;
# permit its one exact version complaint for the same reason.
PATCHED_INITIALIZER = r'''python3 - <<'PY'
from pathlib import Path

path = Path("/usr/lib/orin-audio-common-deb/install_audio_deps.sh")
text = path.read_text(encoding="utf-8")
old = "^naviai-sdk-tts 0\\.1\\.0 has requirement websockets>=14\\.0, but you have websockets 10\\.4\\.$"
new = old + "|^pwdlib 0\\.3\\.1 has requirement typing-extensions>=4\\.16\\.0; python_version < \"3\\.11\", but you have typing-extensions 4\\.15\\.0\\.$"
if text.count(old) != 1:
    raise SystemExit("cannot apply the known Audio pip-check compatibility patch")
path.write_text(text.replace(old, new), encoding="utf-8")
PY
"${SUDO[@]}" "/usr/lib/${AUDIO_COMMON_PACKAGE}/install_audio_deps.sh"'''


def migrate_legacy_avvtn_log() -> None:
    """Preserve a pre-existing directory where the vendor requires a symlink."""
    state = Path("/var/lib/navi-audio")
    legacy = state / "env_runtime/avvtn/log"
    if legacy.is_symlink() or not legacy.exists():
        return
    if not legacy.is_dir():
        raise RuntimeError("Audio runtime path is not a directory or symlink: {}".format(legacy))
    archive_root = state / "logs/avvtn"
    archive_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("legacy-%Y%m%dT%H%M%SZ")
    destination = archive_root / stamp
    suffix = 1
    while destination.exists():
        suffix += 1
        destination = archive_root / "{}-{}".format(stamp, suffix)
    shutil.move(str(legacy), str(destination))
    print("Preserved legacy AVVTN log directory: {} -> {}".format(legacy, destination))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="navi-audio-install-"))
    try:
        migrate_legacy_avvtn_log()
        payload = work / "payload"
        subprocess.run(["/bin/bash", str(args.run), "--noexec", "--target", str(payload)], check=True)
        installer = payload / "install.sh"
        text = installer.read_text(encoding="utf-8")
        count = text.count(FINAL_LAUNCH)
        if count != 1:
            raise RuntimeError("cannot identify the vendor Audio foreground launch (found {})".format(count))
        if text.count(INITIALIZER) != 1:
            raise RuntimeError("cannot identify the vendor Audio dependency initializer")
        text = text.replace(INITIALIZER, PATCHED_INITIALIZER)
        installer.write_text(text.replace(FINAL_LAUNCH, REPLACEMENT), encoding="utf-8")
        subprocess.run(["/bin/bash", str(installer)], cwd=payload, check=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
