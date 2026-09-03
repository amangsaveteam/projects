#!/usr/bin/env bash
# Install target-specific base libraries. Ubuntu ROS targets require a reviewed apt-source DEB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=target-lib.sh
. "${SCRIPT_DIR}/target-lib.sh"

target="${1:-}"
apt_source_deb="${ROS_APT_SOURCE_DEB:-}"
shift || true
while (($#)); do
    case "$1" in
        --ros-apt-source-deb) shift; apt_source_deb="${1:?--ros-apt-source-deb needs a path}" ;;
        --ros-apt-source-deb=*) apt_source_deb="${1#*=}" ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done
[[ -n "${target}" ]] || die "Usage: sudo $0 TARGET [--ros-apt-source-deb FILE]"
require_root
assert_target "${target}"
apt-get update

case "${target}" in
    orin-jazzy)
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nvidia-jetpack ca-certificates software-properties-common locales
        ;;&
    pico-jazzy)
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates software-properties-common locales
        ;;&
    pico-humble)
        [[ -r /opt/ros/humble/setup.bash ]] || die "Pico Humble requires the validated in-house ROS Humble/Focal image before this step"
        dpkg-query -W -f='${db:Status-Status}' ros-humble-rmw-cyclonedds-cpp 2>/dev/null | grep -qx installed || die "Pico Humble image is missing ros-humble-rmw-cyclonedds-cpp"
        ;;&
    rdk-jazzy)
        [[ -r /opt/ros/jazzy/setup.bash ]] || die "RDK Jazzy must be supplied by the approved RDK OS image"
        ;;
esac

if [[ "${target}" == orin-jazzy || "${target}" == pico-jazzy ]]; then
    [[ -n "${apt_source_deb}" && -f "${apt_source_deb}" ]] || die "provide --ros-apt-source-deb for the reviewed ROS Jazzy repository configuration"
    locale-gen en_US.UTF-8
    update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
    add-apt-repository -y universe
    dpkg -i "${apt_source_deb}"
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ros-jazzy-ros-base ros-jazzy-rmw-cyclonedds-cpp
fi
install_manifest "$(target_manifest "${target}")"
note "installed mother-image platform dependencies for ${target}"
