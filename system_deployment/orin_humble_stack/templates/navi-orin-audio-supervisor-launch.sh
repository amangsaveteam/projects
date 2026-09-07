#!/bin/bash
# ROS 2 Humble setup.bash intentionally probes optional variables that may be
# absent.  Do not enable `set -u` before sourcing it (the manual launch flow
# likewise does not use nounset).
set -eo pipefail

# Systemd does not guarantee HOME for the root-owned Supervisor child. ROS 2
# otherwise expands the logging path as ~/.ros and every Audio node fails in
# rclpy.init().  Keep Audio state and ROS logs in explicit writable paths.
export HOME=/var/lib/navi-audio
export ROS_HOME=/var/lib/navi-audio/ros
export ROS_LOG_DIR=/var/log/naviai/audio/ros
install -d -m 0755 "$ROS_HOME" "$ROS_LOG_DIR"

unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH PYTHONPATH
source /opt/ros/humble/setup.bash
# The device common carrier is authoritative for DDS transport.  Audio's
# vendor audio.env contains a stale ROS_DOMAIN_ID=7, whereas Sensor, Robot and
# Chassis use the device domain (normally 72).
source /etc/naviai/Middleware.env
platform_ros_domain_id="${ROS_DOMAIN_ID:?Middleware.env did not set ROS_DOMAIN_ID}"
platform_rmw_implementation="${RMW_IMPLEMENTATION:?Middleware.env did not set RMW_IMPLEMENTATION}"
platform_cyclonedds_uri="${CYCLONEDDS_URI:-}"
source /opt/naviai/venvs/audio/bin/activate
set -a
source /etc/navi-audio/audio.env
set +a
# Preserve Audio credentials and feature settings from audio.env, but never
# allow its packaged DDS values to split the module from the device domain.
export ROS_DOMAIN_ID="$platform_ros_domain_id"
export RMW_IMPLEMENTATION="$platform_rmw_implementation"
if [[ -n "$platform_cyclonedds_uri" ]]; then
  export CYCLONEDDS_URI="$platform_cyclonedds_uri"
else
  unset CYCLONEDDS_URI
fi
cd /var/lib/navi-audio
exec ros2 launch navi_audio_pkg audio_bringup.launch.py \
  mic_device:="plughw:3,0" \
  spk_device:="plughw:2,0" \
  intent_router_mode:=bt \
  enable_face_bridge:=true \
  voiceprint_adapter_mode:=real \
  pico_uri:=ws://192.168.217.66:8765 \
  gesture_backend:=pico \
  pico_gateway_url:=ws://192.168.217.66:8765 \
  enable_background_task_test:=true
