#!/usr/bin/env bash
# Invoked by the generated navi-pico-upperlimb systemd startup wrapper.

set -euo pipefail

# A systemd unit normally starts with a clean environment.  Keep this cleanup
# for manual invocation too, so a sourced ROS 1/Noetic or ROS 2/Foxy shell
# cannot leak into this Humble process.
unset ROS_DISTRO ROS_PACKAGE_PATH ROS_ETC_DIR ROS_ROOT ROS_MASTER_URI ROS_IP ROS_HOSTNAME

strip_path_entries() {
    local variable_name=$1 pattern=$2 old_value new_value entry old_ifs
    old_value="${!variable_name:-}"
    new_value=""
    old_ifs=$IFS
    IFS=':'
    for entry in $old_value; do
        if [[ -n "$entry" && "$entry" != *"$pattern"* ]]; then
            new_value="${new_value:+${new_value}:}${entry}"
        fi
    done
    IFS=$old_ifs
    export "${variable_name}=${new_value}"
}

for path_variable in PATH LD_LIBRARY_PATH PYTHONPATH PKG_CONFIG_PATH CMAKE_PREFIX_PATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH; do
    strip_path_entries "$path_variable" /opt/ros/noetic
    strip_path_entries "$path_variable" /opt/ros/foxy
done

# The generated wrapper sources this file first.  Source it again only when
# this launch helper is run manually.
if [[ "${MIDDLEWARE_PLATFORM:-}" != PICO ]]; then
    # shellcheck disable=SC1091
    . /etc/nav01/Middleware.env
fi
if [[ "${ROS_DISTRO:-}" != humble ]]; then
    # shellcheck disable=SC1091
    . /opt/ros/humble/setup.bash
fi

export ROS_DOMAIN_ID=72
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

# Reuse the PICO common carrier's shared DDS configuration.  Localhost-only
# operation intentionally leaves CycloneDDS to its default transport setup.
if [[ "${ROS_LOCALHOST_ONLY:-0}" = 1 ]]; then
    unset CYCLONEDDS_URI
elif [[ -z "${CYCLONEDDS_URI:-}" ]]; then
    export CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml
fi

append_library_path() {
    local directory=$1
    [[ -d "$directory" ]] || return
    case ":${LD_LIBRARY_PATH:-}:" in
        *":${directory}:"*) ;;
        *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${directory}" ;;
    esac
}

append_library_path /opt/zj_humanoid/lib/logging
append_library_path /opt/zj_humanoid/lib/rtipc_runtime
append_library_path /opt/zj_humanoid/lib/uplimb_runtime

# This service runs as root, so do not use sudo here.  The settings match the
# previous manual launch procedure and are refreshed whenever the service
# starts.
/usr/sbin/sysctl -q -w net.core.rmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.rmem_default=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_default=2147483647

export ROBOT_TYPE="${ROBOT_TYPE:-WA1}"
export CONTROLLER="${CONTROLLER:-v1}"

require_uplimb_config() {
    local variable_name value
    for variable_name in UPLIMB_CONFIG_FILE_PATH UPLIMB_HARDWARE_BODY_FILE_PATH; do
        value="${!variable_name:-}"
        if [[ ! -f "$value" ]]; then
            printf '%s does not exist: %s\n' "$variable_name" "${value:-unset}" >&2
            printf 'ROBOT_TYPE=%s is not available in the installed uplimb_runtime config set.\n' "$ROBOT_TYPE" >&2
            return 1
        fi
    done
}

case "$ROBOT_TYPE" in
    H1|U1|I2|I2-S|I2-D|I2-E)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_upper_body.yaml
        ;;
    WA1|WA1_400L|WA1_400K|U2_WA1|WA1-S|WA1-D|WA1-E|U2-S|U2-D)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_WA1.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA1.yaml
        ;;
    WA2_LS|WA2_TY20|WA2|WA2_L|WA2-S|WA2-P|WA2-D)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_wa2_ls.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA2_LS.yaml
        ;;
    ZYD|ZYD_V1)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_ZYD_V2.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_ZYD_V2.yaml
        ;;
    JK|JK2_V1)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_JK2.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_JK2.yaml
        ;;
    *)
        printf 'Unknown or unsupported Pico ROBOT_TYPE: %s\n' "$ROBOT_TYPE" >&2
        exit 1
        ;;
esac

require_uplimb_config

exec taskset -c 9 ros2 launch uplimb_interface uplimb_interface_node.launch.py \
    controller:="$CONTROLLER" robot_type:="$ROBOT_TYPE"
