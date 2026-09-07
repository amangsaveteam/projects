#!/bin/bash
set -euo pipefail

runtime_dir=/run/naviai/robot
log_dir=/var/log/naviai/robot
config_path="${runtime_dir}/supervisord.conf"
rpc_password_file=/etc/naviai/supervisor-agent/supervisor-rpc.password

install -d -m 0750 "$runtime_dir" "$log_dir" /var/lib/navi/ros
rpc_password=$(tr -d '\r\n' < "$rpc_password_file")
[[ "$rpc_password" =~ ^[[:xdigit:]]{64}$ ]] || { echo "invalid Supervisor RPC credential" >&2; exit 1; }

cat > "$config_path" <<EOF
[supervisord]
nodaemon=true
user=root
logfile=${log_dir}/supervisord.log
pidfile=${runtime_dir}/supervisord.pid

[unix_http_server]
file=${runtime_dir}/supervisor.sock
chmod=0700

[inet_http_server]
port=192.168.217.100:19002
username=agent
password=${rpc_password}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:robot]
command=/bin/bash -lc 'source /etc/naviai/Middleware.env && source /etc/naviai/robot/robot_env.sh && exec ros2 launch orin_robot orin_robot.launch.py'
directory=/var/lib/navi
; Keep the complete logging/runtime contract from the vendor
; navi-orin-robot.service.  Without ROS_LOG_DIR, rclcpp falls back to
; /root/.ros/log under Supervisor and cannot initialize logging.
environment=ROS_DISTRO="humble",ROS_HOME="/var/lib/navi/ros",ROS_LOG_DIR="/var/log/navi/ros",NAVI_ORIN_ROBOT_LOG_DIR="/var/log/navi/robot",NAVI_ORIN_ROBOT_LOG_LEVEL="INFO",RCUTILS_COLORIZED_OUTPUT="0",RCUTILS_LOGGING_USE_STDOUT="1",RCUTILS_LOGGING_BUFFERED_STREAM="1"
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=3
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=${log_dir}/robot.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
EOF
chmod 0600 "$config_path"
exec /usr/bin/supervisord -c "$config_path"
