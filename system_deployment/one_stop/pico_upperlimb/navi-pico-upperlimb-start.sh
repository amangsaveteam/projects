#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Keep the Pico Humble process isolated from any ROS1/Noetic shell.
unset ROS_DISTRO ROS_PACKAGE_PATH ROS_ETC_DIR ROS_ROOT ROS_MASTER_URI ROS_IP ROS_HOSTNAME
strip_path_entries() {
    local var_name="$1" pattern="$2" entry old_ifs old_value new_value=""
    old_value="${!var_name:-}"; old_ifs="$IFS"; IFS=:
    for entry in $old_value; do
        [[ -n "$entry" && "$entry" != *"$pattern"* ]] || continue
        [[ -n "$new_value" ]] && new_value+=:
        new_value+="$entry"
    done
    IFS="$old_ifs"; export "$var_name=$new_value"
}
for path_var in PATH LD_LIBRARY_PATH PYTHONPATH PKG_CONFIG_PATH CMAKE_PREFIX_PATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH; do
    strip_path_entries "$path_var" /opt/ros/noetic
done

# The Humble setup script references optional variables while nounset is
# enabled.  Load the environment with nounset temporarily disabled.
set +u
source /etc/nav01/Middleware.env
source /opt/ros/humble/setup.bash
set -u
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-72}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
if [[ "${ROS_LOCALHOST_ONLY:-0}" == 1 ]]; then unset CYCLONEDDS_URI; fi

export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:/opt/zj_humanoid/lib/logging:/opt/zj_humanoid/lib/rtipc_runtime:/opt/zj_humanoid/lib/uplimb_runtime"
export ROBOT_TYPE=WA-T
export CONTROLLER=v1

case "$ROBOT_TYPE" in
H1|U1|I2|I2-S|I2-D|I2-E)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_upper_body.yaml
    ;;
WA1|WA1_400L|WA1_400K|U2_WA1|WA1-S|WA1-D|WA1-E|U2-S|U2-D)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_WA1.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA1.yaml
    ;;
WA2_LS|WA2_TY20|WA2-S|WA2-D|WA2|WA2_L)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_wa2_ls.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA2_LS.yaml
    ;;
ZYD_V1)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_ZYD_V2.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_ZYD_V1.yaml
    ;;
JK|JK2-V1|JK2_V1)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_JK2_V1.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_JK2_V1.yaml
    ;;
WA-T)
    export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_WA_T.yaml
    export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA_T.yaml
    ;;
*)
    echo "Unsupported upperlimb ROBOT_TYPE: $ROBOT_TYPE" >&2
    exit 1
    ;;
esac

[[ -f "$UPLIMB_CONFIG_FILE_PATH" ]] || { echo "Missing $UPLIMB_CONFIG_FILE_PATH" >&2; exit 1; }
[[ -f "$UPLIMB_HARDWARE_BODY_FILE_PATH" ]] || { echo "Missing $UPLIMB_HARDWARE_BODY_FILE_PATH" >&2; exit 1; }
params_file=/home/nav01/zj_humanoid/config/naviai_default.yaml
[[ -f "$params_file" ]] || { echo "Missing $params_file" >&2; exit 1; }
# Clear global selectors and pass the WA-T profile explicitly.  This prevents
# Middleware.env (which may contain a different robot model) from overriding
# the package configuration.
taskset -c "${UPLIMB_CPU:-9}" env -u ROBOT_TYPE -u CONTROLLER ros2 launch uplimb_interface uplimb_interface_node.launch.py params_file:="$params_file" robot_type:=WA-T controller:=v1 &
launch_pid=$!
trap 'kill "$launch_pid" 2>/dev/null || true; wait "$launch_pid" 2>/dev/null || true' EXIT INT TERM

# The vendor launch file configures and activates the node itself.  Do not issue
# a second configure/activate transition: an already active node rejects it and
# set -e would make Supervisor restart this otherwise healthy process.
wait "$launch_pid"
