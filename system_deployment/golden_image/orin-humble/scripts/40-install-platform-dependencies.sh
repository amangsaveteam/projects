#!/usr/bin/env bash
# Install the platform dependency contract used by Orin Humble modules.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_root
assert_orin_jammy
[[ -f /opt/ros/humble/setup.bash ]] || die "ROS 2 Humble is absent; run 30-install-ros-humble.sh first"
apt-get update
install_list "${GOLDEN_IMAGE_DIR}/packages/platform-runtime.list"
note "platform dependency contract is installed"
