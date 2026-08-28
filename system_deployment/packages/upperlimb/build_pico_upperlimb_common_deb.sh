#!/usr/bin/env bash
# Build the Pico upperlimb offline library carrier on a native Focal/amd64 host.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment_root="$(cd "${script_dir}/../.." && pwd)"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -r /etc/os-release ]] || die '/etc/os-release is unavailable'
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" = ubuntu && "${VERSION_ID:-}" = 20.04 ]] || \
    die "build on native Ubuntu 20.04, found ${ID:-unknown} ${VERSION_ID:-unknown}"
[[ "$(dpkg --print-architecture)" = amd64 && "$(uname -m)" = x86_64 ]] || \
    die "build on amd64/x86_64, found dpkg=$(dpkg --print-architecture) uname=$(uname -m)"

exec python3 "${deployment_root}/common/build_offline_common_bundle.py" \
    --config "${deployment_root}/common/configs/pico-upperlimb-common.json"
