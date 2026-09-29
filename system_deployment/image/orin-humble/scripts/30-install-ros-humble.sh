#!/usr/bin/env bash
# Configure the official ROS 2 repository and install the supported Humble base.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "${SCRIPT_DIR}/lib.sh"

apt_source_deb="${ROS_APT_SOURCE_DEB:-}"
while (($#)); do
    case "$1" in
        --ros-apt-source-deb) shift; apt_source_deb="${1:?--ros-apt-source-deb needs a path}" ;;
        --ros-apt-source-deb=*) apt_source_deb="${1#*=}" ;;
        -h|--help) echo "Usage: sudo $0 --ros-apt-source-deb /path/to/ros2-apt-source_*_jammy_all.deb"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

require_root
assert_orin_jammy
[[ -n "${apt_source_deb}" && -f "${apt_source_deb}" ]] || die "provide the reviewed ROS apt-source deb with --ros-apt-source-deb"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl software-properties-common locales
locale-gen en_US.UTF-8
update-locale LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
add-apt-repository -y universe
dpkg -i "${apt_source_deb}"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ros-humble-ros-base ros-humble-rmw-cyclonedds-cpp
rosdep init 2>/dev/null || true
note "ROS 2 Humble base is installed"
