#!/usr/bin/env bash
# Start the Pico I2 upper-limb interface manually for validation.
#
# This is intentionally not a systemd unit.  It neither installs packages nor
# writes /var/opt/hardware_body.yaml.  The supplied I2 configuration is bind
# mounted over that path only in this process's private mount namespace.

set -euo pipefail

[[ ${EUID} -eq 0 ]] || {
    echo "ERROR: run this upper-limb test as root (sudo $0)" >&2
    exit 1
}

middleware_env=/etc/nav01/Middleware.env
runtime_hardware_body=/var/opt/hardware_body.yaml
hardware_body_source=${1:-}

[[ -n "$hardware_body_source" ]] || {
    echo "Usage: sudo $0 /path/to/i2-hardware_body.yaml" >&2
    exit 2
}

[[ -r "$middleware_env" ]] || {
    echo "ERROR: missing $middleware_env" >&2
    exit 1
}
[[ -r "$hardware_body_source" ]] || {
    echo "ERROR: missing I2 hardware-body source: $hardware_body_source" >&2
    exit 1
}
[[ -e "$runtime_hardware_body" ]] || {
    echo "ERROR: missing mount target $runtime_hardware_body; this test does not create it" >&2
    exit 1
}
[[ -r /opt/ros/humble/setup.bash ]] || {
    echo "ERROR: ROS 2 Humble is not installed" >&2
    exit 1
}

# Do not let a manually sourced ROS 1/other ROS 2 distribution leak in.
unset ROS_DISTRO ROS_PACKAGE_PATH ROS_ETC_DIR ROS_ROOT ROS_MASTER_URI ROS_IP ROS_HOSTNAME
unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH LD_LIBRARY_PATH

set +u
# shellcheck disable=SC1091
. "$middleware_env"
# shellcheck disable=SC1091
. /opt/ros/humble/setup.bash
set -u

export ROS_DOMAIN_ID=72
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_LOCALHOST_ONLY=0
export CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml
export ROBOT_TYPE=I2
export CONTROLLER="${CONTROLLER:-v1}"
export IDDP_ALG_CPU=9
export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
export UPLIMB_HARDWARE_BODY_FILE_PATH="$runtime_hardware_body"

for directory in \
    /opt/zj_humanoid/lib/logging \
    /opt/zj_humanoid/lib/rtipc_runtime \
    /opt/zj_humanoid/lib/uplimb_runtime; do
    [[ -d "$directory" ]] && export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${directory}"
done

[[ -r "$UPLIMB_CONFIG_FILE_PATH" ]] || {
    echo "ERROR: missing $UPLIMB_CONFIG_FILE_PATH" >&2
    exit 1
}
[[ -r /opt/zj_humanoid/lib/logging/liblogging.so ]] || {
    echo "ERROR: missing liblogging.so; install zj-humanoid-logging before this test" >&2
    exit 1
}

for setting in rmem_max rmem_default wmem_max wmem_default; do
    /usr/sbin/sysctl -q -w "net.core.${setting}=2147483647"
done

echo "Starting Pico I2 upper-limb interface (host hardware body remains unchanged)"
exec unshare --mount --fork /bin/bash -c '
    set -euo pipefail
    mount --make-rprivate /
    mount --bind "$1" "$2"
    exec taskset -c 9 ros2 launch uplimb_interface uplimb_interface_node.launch.py \
        controller:="$3" robot_type:="$4"
' -- "$hardware_body_source" "$runtime_hardware_body" "$CONTROLLER" "$ROBOT_TYPE"
