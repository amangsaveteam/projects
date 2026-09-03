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

# The Humble setup files refer to optional variables such as COLCON_TRACE
# without a default value.  Source them with nounset temporarily disabled.
source_humble_runtime() {
    local restore_nounset=0
    case "$-" in
        *u*)
            restore_nounset=1
            set +u
            ;;
    esac

    # shellcheck disable=SC1091
    . /opt/ros/humble/setup.bash

    if [[ "$restore_nounset" -eq 1 ]]; then
        set -u
    fi
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
    source_humble_runtime
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

append_optional_library_path() {
    local directory=$1
    if [[ ! -d "$directory" ]]; then
        printf 'WARNING: optional upperlimb library directory is unavailable: %s\n' "$directory" >&2
        return 0
    fi
    append_library_path "$directory"
}

append_library_path /opt/zj_humanoid/lib/logging
append_library_path /opt/zj_humanoid/lib/rtipc_runtime
append_library_path /opt/zj_humanoid/lib/uplimb_runtime
# Pico V1 runtimes may provide the Pinocchio 3.4 / TinyXML2 ABI 6 bundle in
# this location.  Keep starting when it is absent so a ROS reinstall cannot
# turn an optional search path into an immediate service failure.
append_optional_library_path "${UPLIMB_PINOCCHIO_LIBRARY_DIR:-/home/nav01/CodeFiles/xenomaixddpproject/test_new_lib/lib/pinocchio}"

# This service runs as root, so do not use sudo here.  The settings match the
# previous manual launch procedure and are refreshed whenever the service
# starts.
/usr/sbin/sysctl -q -w net.core.rmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.rmem_default=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_default=2147483647

export ROBOT_TYPE="${ROBOT_TYPE:-WA1}"
export CONTROLLER="${CONTROLLER:-v1}"

# RobotIddp starts SyncFromAlgThread according to IDDP_ALG_CPU.  The legacy
# runtime fallback is CPU 1, but the Pico Xenomai kernel only admits real-time
# tasks on CPUs 0, 8, and 9.  This launcher already reserves CPU 9 for the
# upperlimb process, so use it as the safe default while retaining an explicit
# valid platform override.
case "${IDDP_ALG_CPU:-}" in
    0|8|9)
        ;;
    *)
        export IDDP_ALG_CPU=9
        ;;
esac
printf 'RTIPC SyncFromAlgThread CPU: %s\n' "$IDDP_ALG_CPU"

require_uplimb_config() {
    if [[ ! -f "$UPLIMB_CONFIG_FILE_PATH" ]]; then
        printf 'UPLIMB_CONFIG_FILE_PATH does not exist: %s\n' "${UPLIMB_CONFIG_FILE_PATH:-unset}" >&2
        printf 'ROBOT_TYPE=%s is not available in the installed uplimb_runtime config set.\n' "$ROBOT_TYPE" >&2
        return 1
    fi

    if [[ ! -f "$UPLIMB_HARDWARE_BODY_FILE_PATH" ]]; then
        printf 'UPLIMB_HARDWARE_BODY_FILE_PATH does not exist: %s\n' "${UPLIMB_HARDWARE_BODY_FILE_PATH:-unset}" >&2
        return 1
    fi
}

case "$ROBOT_TYPE" in
    H1|U1)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_upper_body.yaml
        ;;
    I2|I2-S|I2-D|I2-E)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_upper_body.yaml
        ;;
    WA1|WA1_400L|WA1_400K|U2_WA1|WA1-S|WA1-D|WA1-E|U2-S|U2-D)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_WA1.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA1.yaml
        ;;
    WA2|WA2_L)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_wa2_ls.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA2_LS.yaml
        ;;
    WA2_LS|WA2-S|WA2-D)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_wa2_ls.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA2_LS.yaml
        ;;
    WA2_TY20)
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

# uplimb_runtime reads this path directly.  Keep a backup of the legacy field
# file once, then synchronise the model-specific runtime configuration on
# every service start.
runtime_hardware_body=/var/opt/hardware_body.yaml
runtime_hardware_backup=/var/opt/hardware_body.yaml.pre-uplimb-runtime-1.4.0
/usr/bin/install -d -m 0755 /var/opt
if [[ -f "$runtime_hardware_body" && ! -f "$runtime_hardware_backup" ]]; then
    /bin/cp -a "$runtime_hardware_body" "$runtime_hardware_backup"
fi
/usr/bin/install -m 0644 "$UPLIMB_HARDWARE_BODY_FILE_PATH" "$runtime_hardware_body"

exec taskset -c 9 ros2 launch uplimb_interface uplimb_interface_node.launch.py \
    controller:="$CONTROLLER" robot_type:="$ROBOT_TYPE"
