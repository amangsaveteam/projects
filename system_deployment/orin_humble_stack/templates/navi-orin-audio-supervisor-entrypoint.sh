#!/bin/bash
set -euo pipefail

runtime_dir=/run/naviai/audio
log_dir=/var/log/naviai/audio
config_path="${runtime_dir}/supervisord.conf"
rpc_password_file=/etc/naviai/supervisor-agent/supervisor-rpc.password

install -d -m 0750 "$runtime_dir" "$log_dir"
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
port=192.168.217.100:19003
username=agent
password=${rpc_password}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[program:audio]
command=/bin/bash /etc/naviai/audio/supervisor-launch.sh
directory=/var/lib/navi-audio
autostart=true
autorestart=unexpected
exitcodes=0
startsecs=5
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=${log_dir}/audio.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
EOF
chmod 0600 "$config_path"
exec /usr/bin/supervisord -c "$config_path"
