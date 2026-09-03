#!/usr/bin/env bash
# Update an approved Ubuntu mother-image base. RDK BSP updates remain vendor-owned.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=target-lib.sh
. "${SCRIPT_DIR}/target-lib.sh"

target="${1:-}"
[[ -n "${target}" ]] || die "Usage: sudo $0 <orin-jazzy|pico-humble|pico-jazzy|rdk-jazzy>"
require_root
assert_target "${target}"
apt-get update
if [[ "${target}" == rdk-jazzy ]]; then
    note "RDK OS BSP updates are vendor-owned; no dist-upgrade was performed"
else
    DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y
    note "reboot before continuing if the update changed kernel, NVIDIA, or systemd packages"
fi
