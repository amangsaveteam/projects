#!/usr/bin/env bash
# Install only the upper-limb logging dependency.  RTIPC is owned by the I2
# lower-limb bundle and must remain at its bundled 1.4.3 version.

set -euo pipefail

bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
logging_deb="$bundle_root/packages/zj-humanoid-logging_0.1.0focal_amd64.deb"

[[ -r "$logging_deb" ]] || { echo "ERROR: missing $logging_deb" >&2; exit 1; }
dpkg -i "$logging_deb"
