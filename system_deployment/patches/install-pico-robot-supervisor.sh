#!/bin/bash
set -euo pipefail
[ "${EUID}" -eq 0 ] || { echo 'run as root' >&2; exit 1; }
base=/etc/nav01/supervised-stack/robot
runtime=/run/naviai/robot
log=/var/log/naviai/robot
passfile=/etc/nav01/supervisor-agent/supervisor-rpc.password
install -d -m 0755 "$base" /etc/systemd/system "$runtime" "$log" /etc/nav01/supervisor-agent/modules.d
cat > "$base/launch.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
source /etc/profile.d/zj_humanoid.sh
export HOME=/home/nav01
export ROS_HOME=/home/nav01/.ros
export ROS_LOG_DIR=/var/log/naviai/robot/ros
install -d -m 0755 /home/nav01/.ros /var/log/naviai/robot/ros
cd /etc/nav01/robot
exec /etc/nav01/robot/launch.sh
EOF
chmod 0755 "$base/launch.sh"
cat > "$base/supervisor-entrypoint.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
runtime=/run/naviai/robot; log=/var/log/naviai/robot
install -d -m 0755 "$runtime" "$log"
password=$(tr -d '\r\n' < /etc/nav01/supervisor-agent/supervisor-rpc.password)
cat > "$runtime/supervisord.conf" <<CFG
[supervisord]
nodaemon=true
user=root
logfile=$log/supervisord.log
pidfile=$runtime/supervisord.pid
[unix_http_server]
file=$runtime/supervisor.sock
chmod=0700
[inet_http_server]
port=192.168.217.66:19002
username=agent
password=$password
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[program:robot]
command=/bin/bash /etc/nav01/supervised-stack/robot/launch.sh
directory=/etc/nav01/robot
autostart=true
autorestart=unexpected
startsecs=3
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile=/var/log/naviai/robot/robot.log
CFG
exec /usr/bin/supervisord -c "$runtime/supervisord.conf"
EOF
chmod 0755 "$base/supervisor-entrypoint.sh"
cat > /etc/systemd/system/zj-humanoid-pico-robot-supervisor.service <<'EOF'
[Unit]
Description=Navi PICO robot Supervisor
After=network-online.target zj-humanoid-pico-supervisor-agent.service
Wants=network-online.target
[Service]
Type=simple
ExecStart=/bin/bash /etc/nav01/supervised-stack/robot/supervisor-entrypoint.sh
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/nav01/supervisor-agent/modules.d/robot.json <<'EOF'
{"modules":{"robot":{"endpoint":"http://192.168.217.66:19002/RPC2"}}}
EOF
systemctl daemon-reload
systemctl disable --now navi-pico-robot-supervisor.service navi-pico-legged-supervisor.service zj_humanoid.service 2>/dev/null || true
systemctl enable --now zj-humanoid-pico-robot-supervisor.service
