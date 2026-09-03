#!/usr/bin/env bash
# Read-only hardware and platform inventory. Run this immediately after flash.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

output=""
while (($#)); do
    case "$1" in
        --output) shift; output="${1:?--output needs a path}" ;;
        --output=*) output="${1#*=}" ;;
        -h|--help) echo "Usage: $0 [--output INVENTORY.txt]"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

report() {
    echo "Navi Orin Humble hardware inventory"
    echo "generated_at=$(date --iso-8601=seconds)"
    if [[ -r /proc/device-tree/model ]]; then
        echo "model=$(tr -d '\000' </proc/device-tree/model)"
    else
        echo "model=unknown"
    fi
    echo "machine=$(uname -m)"
    echo "debian_architecture=$(dpkg --print-architecture)"
    echo "os_id=$(os_value ID)"
    echo "os_version=$(os_value VERSION_ID)"
    if [[ -r /etc/nv_tegra_release ]]; then
        echo "l4t_release=$(tr '\n' ' ' </etc/nv_tegra_release)"
    else
        echo "l4t_release=missing"
    fi
    echo "nvidia_l4t_core=$(dpkg-query -W -f='${Version}' nvidia-l4t-core 2>/dev/null || echo missing)"
    echo "kernel=$(uname -r)"
    echo "memory_gib=$(awk '/MemTotal/ {printf "%.1f", $2 / 1024 / 1024}' /proc/meminfo)"
    echo "root_filesystem=$(findmnt -n -o SOURCE /)"
    echo "root_available_gib=$(df -BG --output=avail / | awk 'NR == 2 {gsub("G", "", $1); print $1}')"
    echo "network_interfaces=$(ls /sys/class/net | paste -sd, -)"
    echo "block_devices=$(lsblk -dn -o NAME,SIZE,MODEL | tr '\n' ';')"
}

if [[ -n "${output}" ]]; then
    report > "${output}"
    note "wrote ${output}"
else
    report
fi
