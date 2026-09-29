#!/usr/bin/env bash
# Read-only acceptance check before cloning the mother image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=target-lib.sh
. "${SCRIPT_DIR}/target-lib.sh"

target="${1:-}"
[[ -n "${target}" ]] || die "Usage: $0 <orin-jazzy|pico-humble|pico-jazzy|rdk-jazzy>"
assert_target "${target}"
case "${target}" in
    pico-humble) ros_distro=humble ;;
    *) ros_distro=jazzy ;;
esac
[[ -r "/opt/ros/${ros_distro}/setup.bash" ]] || die "ROS ${ros_distro} is missing"
dpkg-query -W -f='${db:Status-Status}' "ros-${ros_distro}-rmw-cyclonedds-cpp" 2>/dev/null | grep -qx installed || die "CycloneDDS RMW is missing"
while IFS= read -r package; do
    [[ -z "${package}" || "${package}" == \#* ]] && continue
    dpkg-query -W -f='${db:Status-Status}' "${package}" 2>/dev/null | grep -qx installed || die "package is not installed: ${package}"
done < "$(target_manifest "${target}")"
echo "PASS: ${target} mother-image platform contract is satisfied"
