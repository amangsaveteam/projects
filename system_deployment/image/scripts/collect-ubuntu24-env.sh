#!/usr/bin/env bash
# Read-only Ubuntu 24.04 environment report for deployment compatibility review.
set -u -o pipefail

output=""
while (($#)); do
    case "$1" in
        --output) shift; output="${1:?--output needs a path}" ;;
        --output=*) output="${1#*=}" ;;
        -h|--help)
            echo "Usage: $0 [--output /tmp/ubuntu24-environment.txt]"
            exit 0
            ;;
        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

section() { printf '\n===== %s =====\n' "$1"; }
run() {
    local label="$1"
    shift
    printf '\n$ %s\n' "${label}"
    "$@" 2>&1 || printf '[unavailable: exit %s]\n' "$?"
}
redact_sources() {
    local file
    for file in /etc/apt/sources.list /etc/apt/sources.list.d/*; do
        [[ -f "${file}" ]] || continue
        printf '\n--- %s ---\n' "${file}"
        # Avoid exposing credentials that may be embedded in a private mirror URL.
        sed -E 's#(https?://)[^/@[:space:]]+@#\1<redacted>@#g' "${file}"
    done
}
report() {
    section "report metadata"
    date --iso-8601=seconds
    id

    section "operating system and kernel"
    run "cat /etc/os-release" cat /etc/os-release
    run "uname -a" uname -a
    run "dpkg --print-architecture" dpkg --print-architecture
    run "dpkg --print-foreign-architectures" dpkg --print-foreign-architectures
    run "uptime" uptime

    section "Jetson / GPU"
    run "cat /proc/device-tree/model" sh -c "tr -d '\\000' </proc/device-tree/model"
    run "cat /etc/nv_tegra_release" cat /etc/nv_tegra_release
    run "dpkg-query NVIDIA packages" sh -c "dpkg-query -W -f='\${Package}\t\${Version}\t\${db:Status-Status}\n' 'nvidia-*' 'cuda-*' 2>/dev/null | sort"
    run "nvidia-smi" nvidia-smi

    section "ROS 2"
    run "ROS installations" sh -c "find /opt/ros -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' 2>/dev/null | sort"
    run "ros-jazzy packages" sh -c "dpkg-query -W -f='\${Package}\t\${Version}\t\${db:Status-Status}\n' 'ros-jazzy-*' 2>/dev/null | sort"
    if [[ -r /opt/ros/jazzy/setup.bash ]]; then
        run "ROS Jazzy runtime" bash -c "source /opt/ros/jazzy/setup.bash && printf 'ROS_DISTRO=%s\\nRMW_IMPLEMENTATION=%s\\n' \"\${ROS_DISTRO:-}\" \"\${RMW_IMPLEMENTATION:-}\" && ros2 --help | head -n 3"
    fi

    section "deployment libraries"
    run "selected Debian packages" sh -c "dpkg-query -W -f='\${Package}\t\${Version}\t\${db:Status-Status}\n' libyaml-cpp0.8 libspdlog-dev libspdlog1.12 libfmt-dev libfmt9 catch2 python3.12-venv python3-pip-whl python3-setuptools-whl network-manager docker.io docker-ce 2>/dev/null | sort"
    run "shared libraries" sh -c "ldconfig -p 2>/dev/null | grep -E 'lib(yaml-cpp|spdlog|fmt|cyclonedds)' | sort || true"
    run "NetworkManager status" systemctl is-active NetworkManager
    run "Docker status" systemctl is-active docker

    section "package sources"
    redact_sources
    run "apt policy ROS source" apt-cache policy ros2-apt-source ros-jazzy-ros-base

    section "storage and networking"
    run "lsblk" lsblk -e7 -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL
    run "df" df -hT
    run "ip addresses" ip -brief address
    run "ip routes" ip route

    section "existing Navi configuration"
    for file in /etc/naviai/Middleware.env /etc/nav01/Middleware.env /etc/zj_humanoid/device.env /etc/zj_humanoid/cyclonedds.xml; do
        [[ -r "${file}" ]] || continue
        printf '\n--- %s ---\n' "${file}"
        sed -E '/(PASSWORD|TOKEN|SECRET|KEY)=/I s/=.*$/=<redacted>/' "${file}"
    done
}

if [[ -n "${output}" ]]; then
    report > "${output}"
    echo "Wrote ${output}"
else
    report
fi
