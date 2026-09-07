#!/bin/bash
# Invoked by the generated navi-pico-upperlimb systemd startup wrapper.

set -euo pipefail

SCRIPT_DIR="/etc/nav01/upperlimb"

# Do not inherit a manually sourced ROS 1 or older ROS 2 environment.
unset ROS_DISTRO ROS_PACKAGE_PATH ROS_ETC_DIR ROS_ROOT ROS_MASTER_URI ROS_IP ROS_HOSTNAME

strip_path_entries() {
    local var_name=$1 pattern=$2 old_value new_value entry old_ifs
    old_value="${!var_name:-}"
    new_value=""
    old_ifs=$IFS
    IFS=':'
    for entry in $old_value; do
        if [[ -n "$entry" && "$entry" != *"$pattern"* ]]; then
            new_value="${new_value:+${new_value}:}${entry}"
        fi
    done
    IFS=$old_ifs
    export "${var_name}=${new_value}"
}

for path_var in PATH LD_LIBRARY_PATH PYTHONPATH PKG_CONFIG_PATH CMAKE_PREFIX_PATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH; do
    strip_path_entries "$path_var" /opt/ros/noetic
    strip_path_entries "$path_var" /opt/ros/foxy
    strip_path_entries "$path_var" /opt/ros/jazzy
done

# Setup files refer to optional variables, so source them with nounset off.
restore_nounset=0
case "$-" in
    *u*) restore_nounset=1; set +u ;;
esac
# shellcheck disable=SC1091
. /opt/ros/humble/setup.bash
if [[ "$restore_nounset" -eq 1 ]]; then
    set -u
fi

export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-72}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

if [[ "${ROS_LOCALHOST_ONLY:-0}" = 1 ]]; then
    unset CYCLONEDDS_URI
else
    # Keep the module DDS configuration with the module, as in the original
    # upperlimb launcher; this file automatically selects an active interface.
    export CYCLONEDDS_URI="file://${SCRIPT_DIR}/cyclonedds.xml"
fi

append_library_path() {
    local directory=$1
    [[ -d "$directory" ]] || return
    case ":${LD_LIBRARY_PATH:-}:
" in
        *":${directory}:
"*) ;;
        *) export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+${LD_LIBRARY_PATH}:}${directory}"
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
# The Pico V1 runtime may use the Pinocchio 3.4 / TinyXML2 ABI 6 bundle from
# this location.  Absence only emits a warning, preventing a missing optional
# search path from terminating the service under `set -e`.
append_optional_library_path "${UPLIMB_PINOCCHIO_LIBRARY_DIR:-/home/nav01/CodeFiles/xenomaixddpproject/test_new_lib/lib/pinocchio}"

# This service runs as root; sudo would cause an interactive failure here.
/usr/sbin/sysctl -q -w net.core.rmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.rmem_default=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_max=2147483647
/usr/sbin/sysctl -q -w net.core.wmem_default=2147483647

export ROBOT_TYPE="${ROBOT_TYPE:-WA1}"
export CONTROLLER="${CONTROLLER:-v1}"

case "${IDDP_ALG_CPU:-}" in
    0|8|9) ;;
    *) export IDDP_ALG_CPU=9 ;;
esac
printf 'RTIPC SyncFromAlgThread CPU: %s\n' "$IDDP_ALG_CPU"

case "$ROBOT_TYPE" in
    H1|U1|I2|I2-S|I2-D|I2-E)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_upper_body.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_upper_body.yaml
        ;;
    WA1|WA1_400L|WA1_400K|U2_WA1|WA1-S|WA1-D|WA1-E|U2-S|U2-D)
        export UPLIMB_CONFIG_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/robot_define_WA1.yaml
        export UPLIMB_HARDWARE_BODY_FILE_PATH=/opt/zj_humanoid/share/uplimb_runtime/config/hardware_body_WA1.yaml
        ;;
    WA2|WA2_L|WA2_LS|WA2_TY20|WA2-S|WA2-P|WA2-D)
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
        printf 'Unknown robot type: %s\n' "$ROBOT_TYPE" >&2
        exit 1
        ;;
esac

if [[ ! -f "$UPLIMB_CONFIG_FILE_PATH" ]]; then
    printf 'UPLIMB_CONFIG_FILE_PATH does not exist: %s\n' "$UPLIMB_CONFIG_FILE_PATH" >&2
    exit 1
fi

if [[ ! -f "$UPLIMB_HARDWARE_BODY_FILE_PATH" ]]; then
    printf 'UPLIMB_HARDWARE_BODY_FILE_PATH does not exist: %s\n' "$UPLIMB_HARDWARE_BODY_FILE_PATH" >&2
    exit 1
fi

# uplimb_runtime reads /var/opt/hardware_body.yaml directly.  Synchronize the
# model-specific runtime configuration before launch so a legacy field file
# cannot be parsed as a Jazzy runtime hardware body.  Keep one backup of the
# previous file for recovery and diagnostics.
runtime_hardware_body=/var/opt/hardware_body.yaml
runtime_hardware_backup=/var/opt/hardware_body.yaml.pre-uplimb-runtime-1.4.0
/usr/bin/install -d -m 0755 /var/opt
if [[ -f "$runtime_hardware_body" && ! -f "$runtime_hardware_backup" ]]; then
    /bin/cp -a "$runtime_hardware_body" "$runtime_hardware_backup"
fi
/usr/bin/install -m 0644 "$UPLIMB_HARDWARE_BODY_FILE_PATH" "$runtime_hardware_body"

# supervisord creates its Unix-control socket itself.  The standalone run
# package must therefore create its parent directory on every start: /run is
# normally cleared at boot, and the generated systemd unit only owns the
# navi-pico-upperlimb log/state directories.
supervisor_runtime_dir=/run/naviai/upperlimb
supervisor_log_dir=/var/log/naviai/upperlimb
supervisor_config="${supervisor_runtime_dir}/supervisord.conf"
supervisor_socket="${supervisor_runtime_dir}/supervisor.sock"
supervisor_rpc_password_file=/etc/nav01/supervisor-agent/supervisor-rpc.password
/usr/bin/install -d -m 0750 "$supervisor_runtime_dir" "$supervisor_log_dir"
# A previous abnormal shutdown can leave the filesystem socket behind.  It is
# safe to remove only this service's socket before starting a new supervisor.
/bin/rm -f "$supervisor_socket"

# Use a quoted heredoc so Supervisor receives its own configuration syntax;
# runtime variables are provided through its environment, which child
# programs inherit by default.
cat > "$supervisor_config" <<'EOF'
[supervisord]
nodaemon=true
user=root
logfile=/var/log/naviai/upperlimb/supervisord.log
pidfile=/run/naviai/upperlimb/supervisord.pid

[unix_http_server]
file=/run/naviai/upperlimb/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///run/naviai/upperlimb/supervisor.sock

[program:upperlimb_interface]
# Invoke the maintained upperlimb launcher instead of attempting shell-style
# $VARIABLE expansion in Supervisor's command field (Supervisor does not run
# this command through a shell).  The launcher validates and applies the
# inherited ROBOT_TYPE/CONTROLLER environment itself.
command=/bin/bash /etc/nav01/upperlimb/upperlimb-launch.sh
autostart=true
autorestart=true
startretries=3
user=root
redirect_stderr=true
stdout_logfile=/var/log/naviai/upperlimb/upperlimb.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
EOF

# The device-level Supervisor Agent provisions this credential.  Keep the
# module Supervisor isolated from the public dashboard while exposing its
# XML-RPC/Web endpoint on the Pico robot network for the Agent.
if [[ -r "$supervisor_rpc_password_file" ]]; then
    supervisor_rpc_password=$(tr -d '\r\n' < "$supervisor_rpc_password_file")
    if [[ "$supervisor_rpc_password" =~ ^[[:xdigit:]]{64}$ ]]; then
        cat >> "$supervisor_config" <<EOF

[inet_http_server]
port=192.168.217.66:19003
username=agent
password=${supervisor_rpc_password}
EOF
    else
        printf 'WARNING: ignoring invalid Supervisor Agent RPC credential: %s\n' "$supervisor_rpc_password_file" >&2
    fi
else
    printf 'WARNING: Supervisor Agent RPC credential is unavailable; upperlimb XML-RPC remains local-only\n' >&2
fi

# The generated configuration contains the module RPC credential.
/bin/chmod 0600 "$supervisor_config"

exec /usr/bin/supervisord -c "$supervisor_config"
