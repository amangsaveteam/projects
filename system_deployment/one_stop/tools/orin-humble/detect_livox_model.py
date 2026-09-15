#!/usr/bin/env python3
"""Discover a connected Livox MID360/MID360S through the installed SDK.

The result is deliberately stored outside ``navigation.env``.  That file is
the operator-owned override; this script only maintains the generated cache
which Middleware.env loads when no manual LIDAR_3D_TYPE was configured.
"""

import argparse
import ctypes
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time


SDK_LIBRARY = "/opt/zj_humanoid/lib/liblivox_lidar_sdk_shared.so"
DEFAULT_ENV_FILE = "/etc/naviai/navigation/navigation.env"
DEFAULT_STATE_FILE = "/etc/naviai/navigation/lidar.auto.env"
SUPPORTED_TYPES = {9: "MID360", 35: "MID360S"}
MANUAL_VALUE = re.compile(
    r"^\s*(?:export\s+)?LIDAR_3D_TYPE\s*=\s*(?:['\"])?([A-Za-z0-9_-]+)(?:['\"])?\s*$"
)


class LivoxLidarInfo(ctypes.Structure):
    _fields_ = [
        ("dev_type", ctypes.c_uint8),
        ("sn", ctypes.c_char * 16),
        ("lidar_ip", ctypes.c_char * 16),
    ]


INFO_CHANGE_CALLBACK = ctypes.CFUNCTYPE(
    None, ctypes.c_uint32, ctypes.POINTER(LivoxLidarInfo), ctypes.c_void_p
)


def manual_model(env_file):
    """Return an explicit valid operator override, if present."""
    path = Path(env_file)
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = MANUAL_VALUE.match(line)
        if match:
            value = match.group(1).upper()
            if value in SUPPORTED_TYPES.values():
                return value
            print(
                "Ignoring unsupported LIDAR_3D_TYPE in {}: {}".format(path, value),
                file=sys.stderr,
            )
    return None


def candidate_host_ips():
    """Return active, non-container IPv4 addresses in deterministic order."""
    try:
        result = subprocess.run(
            ["ip", "-j", "-4", "address", "show", "up"],
            check=True,
            capture_output=True,
            text=True,
        )
        devices = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        return []

    blocked_prefixes = ("lo", "docker", "br-", "veth", "virbr")
    candidates = []
    for device in devices:
        name = str(device.get("ifname", ""))
        if name.startswith(blocked_prefixes):
            continue
        for address in device.get("addr_info", []):
            if address.get("family") != "inet" or address.get("scope") != "global":
                continue
            local = address.get("local")
            try:
                ip = ipaddress.ip_address(local)
            except ValueError:
                continue
            if not ip.is_loopback and not ip.is_unspecified:
                candidates.append(str(ip))
    return candidates


def sdk_config(model, host_ip, lidar_ip):
    """Build the SDK's documented per-model network configuration."""
    lidar_network = {
        "cmd_data_port": 56100,
        "push_msg_port": 56200,
        "point_data_port": 56300,
        "imu_data_port": 56400,
        "log_data_port": 56500,
    }
    host_network = {
        "cmd_data_ip": host_ip,
        "cmd_data_port": 56101,
        "push_msg_ip": host_ip,
        "push_msg_port": 56201,
        "point_data_ip": host_ip,
        "point_data_port": 56301,
        "imu_data_ip": host_ip,
        "imu_data_port": 56401,
        "log_data_ip": "",
        "log_data_port": 56501,
    }
    lidar = {
        "ip": lidar_ip,
        "pcl_data_type": 1,
        "pattern_mode": 0,
        "extrinsic_parameter": {"roll": 0.0, "pitch": 0.0, "yaw": 0.0, "x": 0, "y": 0, "z": 0},
    }
    result = {"lidar_summary_info": {"lidar_type": 8}, "lidar_configs": [lidar]}
    if model == "MID360":
        result["MID360"] = {"lidar_net_info": lidar_network, "host_net_info": host_network}
    else:
        result["Mid360s"] = {
            "lidar_net_info": lidar_network,
            "host_net_info": [{
                "host_ip": host_ip,
                "cmd_data_port": 56101,
                "push_msg_port": 56201,
                "point_data_port": 56301,
                "imu_data_port": 56401,
                "log_data_port": 56501,
            }],
        }
    return result


def probe(host_ip, timeout, config=None):
    """Return a supported model discovered by one SDK view-mode instance."""
    try:
        library = ctypes.CDLL(SDK_LIBRARY)
    except OSError as error:
        print("Livox SDK is unavailable: {}".format(error), file=sys.stderr)
        return None

    library.LivoxLidarSdkInit.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
    library.LivoxLidarSdkInit.restype = ctypes.c_bool
    library.LivoxLidarSdkStart.argtypes = []
    library.LivoxLidarSdkStart.restype = ctypes.c_bool
    library.LivoxLidarSdkUninit.argtypes = []
    library.LivoxLidarSdkUninit.restype = None
    library.SetLivoxLidarInfoChangeCallback.argtypes = [INFO_CHANGE_CALLBACK, ctypes.c_void_p]
    library.SetLivoxLidarInfoChangeCallback.restype = None
    library.DisableLivoxSdkConsoleLogger.argtypes = []
    library.DisableLivoxSdkConsoleLogger.restype = None

    detected = []
    event = threading.Event()

    @INFO_CHANGE_CALLBACK
    def on_lidar(_handle, info_pointer, _client_data):
        if not info_pointer:
            return
        model = SUPPORTED_TYPES.get(info_pointer.contents.dev_type)
        if model:
            detected.append(model)
            event.set()

    try:
        library.DisableLivoxSdkConsoleLogger()
        # A configuration path is the stable SDK initialization route.  The
        # package's view mode (null path) crashes in the supplied SDK after a
        # detected device responds; callers are therefore required to provide
        # a known lidar IP and use this configuration path.
        config_argument = os.fsencode(config)
        host_argument = b""
        if not library.LivoxLidarSdkInit(config_argument, host_argument, None):
            print("Livox SDK initialization failed on {}".format(host_ip), file=sys.stderr)
            return None
        library.SetLivoxLidarInfoChangeCallback(on_lidar, None)
        library.LivoxLidarSdkStart()
        event.wait(timeout)
        return detected[0] if detected else None
    finally:
        library.LivoxLidarSdkUninit()


def write_state(state_file, model):
    destination = Path(state_file)
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    content = (
        "# Generated by detect_livox_model.py; do not edit this file.\n"
        "# Set LIDAR_3D_TYPE in navigation.env to override automatic detection.\n"
        "export LIDAR_3D_TYPE={}\n".format(model)
    )
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=destination.parent, delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    temporary.chmod(0o644)
    temporary.replace(destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--timeout", type=float, default=4.0)
    parser.add_argument("--host-ip", help="Probe only this local IPv4 address")
    parser.add_argument(
        "--lidar-ip",
        help="Use stable SDK configuration mode for this known lidar IPv4 address",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Report the detected model without creating or changing the state file",
    )
    arguments = parser.parse_args(argv)
    if arguments.timeout <= 0:
        parser.error("--timeout must be positive")

    override = manual_model(arguments.env_file)
    if override:
        print("Livox model uses manual override: {}".format(override))
        return 0

    if not Path(SDK_LIBRARY).is_file():
        print("Livox SDK library is missing: {}".format(SDK_LIBRARY), file=sys.stderr)
        return 1

    if not arguments.lidar_ip:
        print("--lidar-ip is required; SDK view-mode discovery is disabled because it crashes this SDK build", file=sys.stderr)
        return 2

    hosts = [arguments.host_ip] if arguments.host_ip else candidate_host_ips()
    if not hosts:
        print("No active IPv4 interface available for Livox discovery", file=sys.stderr)
        return 1
    for host_ip in hosts:
        requested_models = ("MID360", "MID360S")
        for requested_model in requested_models:
            config_path = None
            try:
                if requested_model:
                    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8", delete=False) as handle:
                        json.dump(sdk_config(requested_model, host_ip, arguments.lidar_ip), handle)
                        config_path = handle.name
                model = probe(host_ip, arguments.timeout, config_path)
            finally:
                if config_path:
                    Path(config_path).unlink(missing_ok=True)
            if model:
                if not arguments.no_write:
                    write_state(arguments.state_file, model)
                suffix = " (state not written)" if arguments.no_write else ""
                print("Livox model detected on {}: {}{}".format(host_ip, model, suffix))
                return 0
    print("No MID360 or MID360S detected; retaining any previous automatic result", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
