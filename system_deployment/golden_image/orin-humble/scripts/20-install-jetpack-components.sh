#!/usr/bin/env bash
# Install JetPack 6.1 user-space components matching the flashed L4T R36.4 BSP.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

require_root
require_reboot_after_prepare
assert_orin_jammy
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nvidia-jetpack
note "JetPack components are installed"
