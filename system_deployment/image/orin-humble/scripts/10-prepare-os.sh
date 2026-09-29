#!/usr/bin/env bash
# Update the flashed Ubuntu 22.04 base before ROS packages are introduced.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_root
assert_orin_jammy
mkdir -p "${STATE_DIR}"
note "updating Ubuntu and NVIDIA packages; a reboot is mandatory afterwards"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y
cat /proc/sys/kernel/random/boot_id > "${STATE_DIR}/reboot-required"
note "upgrade completed. Reboot now, then continue with 20-install-jetpack-components.sh"
