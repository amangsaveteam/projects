#!/usr/bin/env python3
"""Enumerate Orbbec GMSL cameras and supervise the two wrist launches."""
import argparse
import os
import re
import signal
import subprocess
import sys
import time
import yaml


def apply_stream_settings(path, settings):
    streams = settings.get("streams", {})
    color, depth = streams.get("color", {}), streams.get("depth", {})
    replacements = {
        "depth_registration": str(bool(streams.get("depth_registration", True))).lower(),
        "color_width": str(color.get("width", 1280)),
        "color_height": str(color.get("height", 720)),
        "color_fps": str(color.get("fps", 30)),
        "depth_width": str(depth.get("width", 1280)),
        "depth_height": str(depth.get("height", 720)),
        "depth_fps": str(depth.get("fps", 30)),
    }
    with open(path, encoding="utf-8") as stream:
        text = stream.read()
    for key, value in replacements.items():
        text, count = re.subn(rf"(?m)^{re.escape(key)}:\s*.*$", f"{key}: {value}", text, count=1)
        if count == 0:
            text = f"{key}: {value}\n" + text
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)


def enumerate_devices():
    result = subprocess.run(["ros2", "run", "orbbec_camera", "list_devices_node"], text=True, capture_output=True, check=True)
    output = "\n".join(stream for stream in (result.stdout, result.stderr) if stream)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    devices, port, name = [], None, None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        # rosout prefixes logger lines with ``[INFO] ...``; fields are not
        # necessarily at column zero when list_devices_node is run under a
        # supervisor.
        match = re.search(
            r"(?:parsed\s+gmsl\s+port\s+id|usb\s+port|gmsl\s+port)"
            r"\s*:\s*(gmsl\d+-\d+)\b",
            line,
            re.I,
        )
        if match:
            port = match.group(1)
        match = re.search(r"(?:^|\s)name:\s*(.+)$", line, re.I)
        if match:
            name = match.group(1).strip()
        if port and name and "gemini 305g" in name.lower():
            devices.append((port, name))
            port = name = None
    return sorted(set(devices))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/naviai/sensor/orb_config.yaml")
    parser.add_argument("--camera-config", default="")
    args = parser.parse_args()
    with open(args.config, encoding="utf-8") as stream:
        settings = yaml.safe_load(stream) or {}
    wrists = settings.get("wrists", settings)
    camera_config = args.camera_config or settings.get(
        "camera_config_file",
        "/opt/ros/humble/share/orbbec_camera/config/gemini305_dual_color.yaml",
    )
    apply_stream_settings(camera_config, settings)
    launch_delay = float(settings.get("launch_delay_sec", 5.0))
    found = enumerate_devices()
    left, right = wrists.get("left_gmsl_port", ""), wrists.get("right_gmsl_port", "")
    if wrists.get("assignment_mode", "manual") == "auto":
        if len(found) != 2:
            raise SystemExit(f"expected exactly 2 Gemini 305g cameras, found {found}")
        left, right = found[0][0], found[1][0]
    if not left or not right or left == right:
        raise SystemExit("left/right GMSL ports must be configured and different")
    available = {port for port, _ in found}
    if left not in available or right not in available:
        raise SystemExit(f"configured GMSL ports missing: {(left, right)}, found {sorted(available)}")
    mapping = wrists.get("resolved_mapping_file", "/run/navi-sensor-host/orbbec-wrist-mapping.yaml")
    with open(mapping, "w", encoding="utf-8") as stream:
        stream.write(f"left_gmsl_port: {left}\nright_gmsl_port: {right}\n")
    processes = []
    launch_environment = os.environ.copy()
    launch_environment["ROS_NAMESPACE"] = "/zj_humanoid/sensor"
    processes.append(subprocess.Popen([
        "ros2", "launch", "/usr/lib/naviai/sensor/orbbec_multi_gmsl_camera.launch.py",
        f"left_gmsl_port:={left}", f"right_gmsl_port:={right}",
        f"config_file_path:={camera_config}",
    ], env=launch_environment, start_new_session=True, stdout=sys.stdout, stderr=sys.stderr))
    try:
        return processes[0].wait()
    finally:
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
