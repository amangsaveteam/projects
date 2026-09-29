#!/usr/bin/env bash
set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DEPLOYMENT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

die() { echo "ERROR: $*" >&2; exit 1; }
note() { echo "INFO: $*"; }
require_root() { [[ "${EUID}" -eq 0 ]] || die "run this script with sudo"; }

target_manifest() {
    case "$1" in
        orin-jazzy) echo "${DEPLOYMENT_DIR}/common/manifests/orin/apt-packages.tsv" ;;
        pico-humble) echo "${DEPLOYMENT_DIR}/common/manifests/pico/apt-packages.tsv" ;;
        pico-jazzy) echo "${DEPLOYMENT_DIR}/common/manifests/pico-jazzy/apt-packages.tsv" ;;
        rdk-jazzy) echo "${DEPLOYMENT_DIR}/common/manifests/rdk/apt-packages.tsv" ;;
        *) die "unknown target: $1" ;;
    esac
}

assert_target() {
    local target="$1" id version arch machine
    # shellcheck disable=SC1091
    . /etc/os-release
    id="${ID,,}"; version="${VERSION_ID:-}"; arch="$(dpkg --print-architecture)"; machine="$(uname -m)"
    case "${target}" in
        orin-jazzy)
            [[ "${id}" == ubuntu && "${version}" == 24.04 && "${arch}" == arm64 && "${machine}" == aarch64 ]] || die "orin-jazzy requires Ubuntu 24.04 arm64/aarch64"
            [[ -r /etc/nv_tegra_release ]] || die "orin-jazzy requires a Jetson L4T BSP"
            ;;
        pico-humble) [[ "${id}" == ubuntu && "${version}" == 20.04 && "${arch}" == amd64 && "${machine}" == x86_64 ]] || die "pico-humble requires Ubuntu 20.04 amd64/x86_64" ;;
        pico-jazzy) [[ "${id}" == ubuntu && "${version}" == 24.04 && "${arch}" == amd64 && "${machine}" == x86_64 ]] || die "pico-jazzy requires Ubuntu 24.04 amd64/x86_64" ;;
        rdk-jazzy) [[ "${id}" == "rdk os" && "${version}" == V5.1.0 && "${arch}" == arm64 && "${machine}" == aarch64 ]] || die "rdk-jazzy requires RDK OS V5.1.0 arm64/aarch64" ;;
    esac
}

install_manifest() {
    local manifest="$1"
    mapfile -t packages < <(awk -F '\t' 'NF && $1 !~ /^#/ { print $1 }' "${manifest}")
    ((${#packages[@]})) || return 0
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
}
