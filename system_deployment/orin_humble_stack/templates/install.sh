#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
robot_type=""
action=install

usage() {
  cat <<'EOF'
Usage: navi_orin_humble_modules_supervisor-*.run -- [--robot-type TYPE] [--pretest|--verify|--info]

Installs the offline Orin Humble Sensor, Robot, Chassis and Audio run packages,
then exposes their independent Supervisors on 192.168.217.100 ports 19001-19004.
The dashboard is available without a token at http://<Orin-LAN-IP>:9080.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --) ;;
    --robot-type) shift; robot_type="${1:?--robot-type needs a value}" ;;
    --robot-type=*) robot_type="${1#--robot-type=}" ;;
    --pretest|--verify|--info) action="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ "$action" == --info ]]; then
  echo "Navi Orin Humble supervised modules: Sensor Robot Chassis Audio"
  exit 0
fi
if [[ "$action" == --verify ]]; then
  (cd "$root" && sha256sum -c payloads.sha256)
  exit $?
fi
if [[ "$action" == --pretest ]]; then
  (cd "$root" && sha256sum -c payloads.sha256)
  echo "Requires Ubuntu 22.04 arm64/aarch64, navi-common-dep and supervisor."
  echo "Audio installation validates /etc/navi-audio/audio.env and its configured devices."
  exit 0
fi

[[ $EUID -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }
[[ "$robot_type" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "ERROR: --robot-type TYPE is required" >&2; exit 2; }
. /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" == 22.04 ]] || { echo "ERROR: this package requires Ubuntu 22.04" >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) echo "ERROR: this package requires arm64/aarch64" >&2; exit 1;; esac
command -v supervisord >/dev/null || { echo "ERROR: install the supervisor package from the Orin base image first" >&2; exit 1; }
dpkg-query -W -f='${db:Status-Status}' navi-common-dep 2>/dev/null | grep -qx installed || {
  echo "ERROR: navi-common-dep must be installed and configured first" >&2; exit 1;
}
(cd "$root" && sha256sum -c payloads.sha256)

run_package() {
  local label=$1
  shift
  echo "========== INSTALL ${label} =========="
  /bin/bash "$@"
}

# Install our Agent first so all four module Supervisor instances use one
# persistent RPC credential.  It is stopped before Robot installs the legacy
# vendor Agent, which otherwise would temporarily bind 9080.
run_package "SUPERVISOR AGENT" "$root/payloads/agent.run" install --device ORIN --robot-type "$robot_type"
systemctl stop navi-orin-supervisor-agent.service || true
run_package "CHASSIS" "$root/payloads/chassis.run" install --device ORIN --robot-type "$robot_type"
run_package "SENSOR" "$root/payloads/sensor.run" -- --robot-type "$robot_type"
python3 "$root/helpers/configure_sensor_rpc.py" \
  --config /etc/naviai/navi-sensor-host-supervisor.conf \
  --password-file /etc/naviai/supervisor-agent/supervisor-rpc.password

run_package "ROBOT" "$root/payloads/robot.run"
# The Robot artifact carries an older read-only dashboard.  The current Agent
# owns 9080 and provides aggregation plus start/stop/restart/log actions.
systemctl disable --now navi-supervisor-agent.service || true
systemctl disable --now navi-orin-robot.service || true
install -d -m 0755 /etc/naviai/robot /etc/naviai/audio /etc/naviai/supervisor-agent/modules.d
install -m 0755 "$root/helpers/navi-orin-robot-supervisor-entrypoint.sh" /etc/naviai/robot/supervisor-entrypoint.sh
install -m 0644 "$root/helpers/navi-orin-robot-supervisor.service" /etc/systemd/system/navi-orin-robot-supervisor.service

echo "========== INSTALL AUDIO =========="
python3 "$root/helpers/install_audio_without_start.py" "$root/payloads/audio.run"
install -m 0755 "$root/helpers/navi-orin-audio-supervisor-entrypoint.sh" /etc/naviai/audio/supervisor-entrypoint.sh
install -m 0755 "$root/helpers/navi-orin-audio-supervisor-launch.sh" /etc/naviai/audio/supervisor-launch.sh
install -m 0644 "$root/helpers/navi-orin-audio-supervisor.service" /etc/systemd/system/navi-orin-audio-supervisor.service
install -m 0644 "$root/helpers/sensor.json" /etc/naviai/supervisor-agent/modules.d/sensor.json
install -m 0644 "$root/helpers/robot.json" /etc/naviai/supervisor-agent/modules.d/robot.json
install -m 0644 "$root/helpers/audio.json" /etc/naviai/supervisor-agent/modules.d/audio.json

systemctl daemon-reload
systemctl enable --now navi-sensor-host.service
systemctl restart navi-sensor-host.service
systemctl enable --now navi-orin-robot-supervisor.service
systemctl enable --now navi-orin-audio-supervisor.service
systemctl enable --now navi-orin-chassis.service
systemctl restart navi-orin-chassis.service
systemctl enable --now navi-orin-supervisor-agent.service
systemctl restart navi-orin-supervisor-agent.service

echo "Installed. Open http://<Orin LAN IP>:9080 (for example http://172.16.9.91:9080)."
