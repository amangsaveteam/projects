#!/bin/bash
# Compatibility verifier for Sensor packages released with the old parent path.
set -euo pipefail

common_installer=/usr/sbin/install_common_deps.sh
if ! dpkg-query -W -f='${Status}' navi-common-dep 2>/dev/null | grep -Fxq 'install ok installed'; then
    echo 'ERROR: navi-common-dep is not installed' >&2
    exit 1
fi
if [[ ! -x "$common_installer" ]]; then
    echo "ERROR: common dependency installer is missing: $common_installer" >&2
    exit 1
fi

if [[ "${1:-}" == --verify-only ]]; then
    [[ $# -eq 1 ]] || { echo 'ERROR: --verify-only accepts no additional arguments' >&2; exit 2; }
    exit 0
fi
[[ $# -eq 0 ]] || { echo "ERROR: unsupported argument: $1" >&2; exit 2; }
exec "$common_installer"
