#!/bin/bash
# Starts the chassis ROS launch under its own Supervisor instance.

set -euo pipefail

runtime_dir=/run/naviai/chassis
log_dir=/var/log/naviai/chassis
config_path="${runtime_dir}/supervisord.conf"
socket_path="${runtime_dir}/supervisor.sock"
rpc_password_file=/etc/naviai/supervisor-agent/supervisor-rpc.password

/usr/bin/install -d -m 0750 "$runtime_dir" "$log_dir"
/bin/rm -f "$socket_path"

cat > "$config_path" <<'EOF'
[supervisord]
nodaemon=true
user=root
logfile=/var/log/naviai/chassis/supervisord.log
pidfile=/run/naviai/chassis/supervisord.pid

[unix_http_server]
file=/run/naviai/chassis/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///run/naviai/chassis/supervisor.sock

[program:chassis]
; ros2 is provided by the Humble installation under /opt/ros, not /usr/bin.
; Load the shared platform environment exactly as the other Orin ROS modules do.
command=/bin/bash -lc 'unset ROS_DOMAIN_ID RMW_IMPLEMENTATION CYCLONEDDS_URI; source /etc/naviai/Middleware.env; exec ros2 launch chassis chassis.launch.py namespace:=zj_humanoid'
autostart=true
autorestart=true
startretries=3
startsecs=3
user=root
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=/var/log/naviai/chassis/chassis.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
EOF

# The Agent creates this secret.  If the chassis package is installed first,
# it still starts normally and becomes remotely visible after the Agent
# package is installed and restarts this service.
if [[ -r "$rpc_password_file" ]]; then
    rpc_password=$(tr -d '\r\n' < "$rpc_password_file")
    if [[ "$rpc_password" =~ ^[[:xdigit:]]{64}$ ]]; then
        cat >> "$config_path" <<EOF

[inet_http_server]
port=192.168.217.100:19004
username=agent
password=${rpc_password}
EOF
    else
        printf 'WARNING: ignoring invalid Supervisor Agent RPC credential: %s\n' "$rpc_password_file" >&2
    fi
else
    printf 'WARNING: Supervisor Agent RPC credential is unavailable; chassis XML-RPC remains local-only\n' >&2
fi

/bin/chmod 0600 "$config_path"
exec /usr/bin/supervisord -c "$config_path"
