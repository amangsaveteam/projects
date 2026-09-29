#!/usr/bin/env bash
# Read-only release acceptance checks. Run before cloning the golden image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

assert_orin_jammy
[[ -r /opt/ros/humble/setup.bash ]] || die "ROS 2 Humble setup is missing"

while IFS= read -r package; do
    [[ -z "${package}" || "${package}" == \#* ]] && continue
    dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null | grep -qx installed || die "package is not installed: ${package}"
done < "${GOLDEN_IMAGE_DIR}/packages/platform-runtime.list"

source /opt/ros/humble/setup.bash
command -v ros2 >/dev/null || die "ros2 command is unavailable"

echo "PASS: Orin Humble golden image contract is satisfied"
echo "model=$(tr -d '\000' </proc/device-tree/model 2>/dev/null || echo unknown)"
echo "l4t=$(l4t_version) ros=humble"
