#!/usr/bin/env python3
"""Install vendor Vision while leaving an installed shared message package owned by its provider."""
import argparse
from pathlib import Path
import subprocess
import tempfile


DECLARATION = 'readonly ROS_PACKAGES=(ros-humble-navi-vision-msgs ros-humble-upperlimb-msgs ros-humble-navi-vision-pkg)'
REPLACEMENT = '''# Shared messages are not owned by this Vision transaction when already installed.
if [[ "$(dpkg-query -W -f='${db:Status-Status}' ros-humble-upperlimb-msgs 2>/dev/null || true)" == installed ]]; then
  echo "vision-installer: preserving installed ros-humble-upperlimb-msgs (excluded from install and rollback)"
  readonly ROS_PACKAGES=(ros-humble-navi-vision-msgs ros-humble-navi-vision-pkg)
else
  readonly ROS_PACKAGES=(ros-humble-navi-vision-msgs ros-humble-upperlimb-msgs ros-humble-navi-vision-pkg)
fi'''


def adapt_installer(text):
    if text.splitlines().count(DECLARATION) != 1:
        raise RuntimeError('unsupported Vision installer: expected one ROS_PACKAGES declaration')
    return text.replace(DECLARATION, REPLACEMENT, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='navi-vision-install-') as temporary:
        payload = Path(temporary) / 'payload'
        subprocess.run(['/bin/bash', str(args.run), '--noexec', '--target', str(payload)], check=True)
        installer = payload / 'install.sh'
        installer.write_text(adapt_installer(installer.read_text()))
        subprocess.run(['/bin/bash', '-n', str(installer)], check=True)
        subprocess.run(['/bin/bash', str(installer), *args.arguments], cwd=payload, check=True)


if __name__ == '__main__':
    main()
