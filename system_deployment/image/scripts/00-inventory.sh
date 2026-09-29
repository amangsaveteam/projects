#!/usr/bin/env bash
# Read-only inventory for all mother-image targets except orin-humble.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=target-lib.sh
. "${SCRIPT_DIR}/target-lib.sh"

target="${1:-}"
[[ -n "${target}" ]] || die "Usage: $0 <orin-jazzy|pico-humble|pico-jazzy|rdk-jazzy>"
assert_target "${target}"
echo "target=${target}"
echo "os=$(grep -E '^(ID|VERSION_ID)=' /etc/os-release | tr '\n' ' ')"
echo "architecture=$(dpkg --print-architecture) machine=$(uname -m)"
echo "root=$(findmnt -n -o SOURCE /) available=$(df -h --output=avail / | awk 'NR == 2 {print $1}')"
[[ -r /etc/nv_tegra_release ]] && echo "l4t=$(tr '\n' ' ' </etc/nv_tegra_release)"
[[ -r /opt/ros/humble/setup.bash ]] && echo "ros_humble=present"
[[ -r /opt/ros/jazzy/setup.bash ]] && echo "ros_jazzy=present"
