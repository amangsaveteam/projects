#!/usr/bin/env python3
"""Adapt Vision transactions that share an installed upperlimb message package."""
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

SAME_RELEASE_PREVIOUS = '''  elif [[ -n "${old_release_id}" && -d "${old_venv}" && -d "${old_runtime}" &&
          "${old_venv##*/}" == "vision-${old_release_id}" &&
          "${old_runtime##*/}" == "${old_release_id}" ]]; then'''
SAME_RELEASE_REPLACEMENT = '''  elif [[ "${old_release_id}" != "${release_id}" && -n "${old_release_id}" && -d "${old_venv}" && -d "${old_runtime}" &&
          "${old_venv##*/}" == "vision-${old_release_id}" &&
          "${old_runtime##*/}" == "${old_release_id}" ]]; then'''


def replace_once(text, declaration, replacement, description):
    if text.count(declaration) != 1:
        raise RuntimeError('unsupported Vision installer: expected one ' + description)
    return text.replace(declaration, replacement, 1)


def adapt_installer(text):
    text = replace_once(text, DECLARATION, REPLACEMENT, 'ROS_PACKAGES declaration')
    return replace_once(
        text,
        SAME_RELEASE_PREVIOUS,
        SAME_RELEASE_REPLACEMENT,
        'same-release previous-release branch',
    )


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
