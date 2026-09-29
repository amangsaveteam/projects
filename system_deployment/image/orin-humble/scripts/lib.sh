#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly GOLDEN_IMAGE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly DEPLOYMENT_DIR="$(cd "${GOLDEN_IMAGE_DIR}/../.." && pwd)"
readonly STATE_DIR="/var/lib/navi-golden-image"

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "INFO: $*"; }

require_root() { [[ "${EUID}" -eq 0 ]] || die "run this script with sudo"; }

os_value() {
    local key="$1"
    [[ -r /etc/os-release ]] || die "/etc/os-release is missing"
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${key}" in
        ID) printf '%s\n' "${ID:-}" ;;
        VERSION_ID) printf '%s\n' "${VERSION_ID:-}" ;;
        *) die "unsupported os-release key: ${key}" ;;
    esac
}

l4t_version() {
    dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null | sed -E 's/^[^0-9]*([0-9]+\.[0-9]+).*/\1/' || true
}

assert_orin_jammy() {
    [[ "$(uname -m)" == "aarch64" ]] || die "expected aarch64 Orin, found $(uname -m)"
    [[ "$(dpkg --print-architecture)" == "arm64" ]] || die "expected Debian arm64"
    [[ "$(os_value ID)" == "ubuntu" && "$(os_value VERSION_ID)" == "22.04" ]] || die "expected Ubuntu 22.04"
    [[ -r /etc/nv_tegra_release ]] || die "not a Jetson L4T installation: /etc/nv_tegra_release is absent"
    [[ "$(l4t_version)" == "36.4" ]] || die "expected JetPack 6.1 / L4T R36.4; found $(l4t_version || echo unknown)"
}

require_reboot_after_prepare() {
    local marker="${STATE_DIR}/reboot-required" previous_boot current_boot
    [[ -f "${marker}" ]] || return 0
    previous_boot="$(cat "${marker}")"
    current_boot="$(cat /proc/sys/kernel/random/boot_id)"
    [[ "${previous_boot}" != "${current_boot}" ]] || die "system upgrade completed; reboot the Orin before running this step"
    rm -f "${marker}"
}

install_list() {
    local list="$1"
    mapfile -t packages < <(sed -E '/^[[:space:]]*(#|$)/d' "${list}")
    ((${#packages[@]})) || die "no packages found in ${list}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
}

is_supported_robot_type() {
    case "$1" in
        H1|U1|U2_WA1|I2|WA1|WA1_400L|WA1_400K|WA2_LS|WA2_TY20|WA2|WA2_L|I2-S|I2-D|I2-E|I3-S|WA1-S|WA1-D|WA1-E|WA2-S|WA2-P|WA2-D|U2-S|U2-D|ZYD|ZYD_V1|JK|JK2_V1) return 0 ;;
        *) return 1 ;;
    esac
}
