#!/usr/bin/env python3
"""Parameterized build and device-configuration entry point."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


COMMON_DIR = Path(__file__).resolve().parent
DEFAULT_DEVICE_CONFIG = Path("/etc/zj_humanoid/device.env")
ALLOWED_KEYS = (
    "ZJ_DEVICE",
    "ROBOT_TYPE",
    "ROBOT_NAME",
    "ZJ_VERSION",
    "ROS_DOMAIN_ID",
    "CYCLONEDDS_URI",
    "COMPOSE_PROFILES",
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def targets() -> dict[str, dict[str, Any]]:
    return load_json(COMMON_DIR / "configs/targets.json")["targets"]


def robot_types() -> dict[str, dict[str, Any]]:
    return load_json(COMMON_DIR / "configs/robot-types.json")["robot_types"]


def target_config(name: str) -> dict[str, Any]:
    available = targets()
    if name not in available:
        raise ValueError(f"unknown target {name!r}; choose one of: {', '.join(sorted(available))}")
    return available[name]


def read_device_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ALLOWED_KEYS:
            values[key] = value
    return values


def validate_device_config(values: dict[str, str]) -> None:
    if values.get("ZJ_DEVICE") not in {"ORIN", "PICO", "RDK"}:
        raise ValueError("ZJ_DEVICE must be ORIN, PICO, or RDK")
    robot_type = values.get("ROBOT_TYPE", "")
    if robot_type not in robot_types():
        raise ValueError(f"unsupported ROBOT_TYPE {robot_type!r}")
    domain = values.get("ROS_DOMAIN_ID", "72")
    if not domain.isdecimal() or not 0 <= int(domain) <= 232:
        raise ValueError("ROS_DOMAIN_ID must be an integer from 0 to 232")
    uri = values.get("CYCLONEDDS_URI", "")
    if uri and not uri.startswith("file:///"):
        raise ValueError("CYCLONEDDS_URI must start with file:///")
    for key in ("ROBOT_NAME", "ZJ_VERSION", "COMPOSE_PROFILES"):
        value = values.get(key, "")
        if any(character.isspace() for character in value):
            raise ValueError(f"{key} cannot contain whitespace")


def write_device_config(path: Path, values: dict[str, str]) -> None:
    validate_device_config(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Managed by deploy_common.py. Edit with the same command or preserve KEY=VALUE syntax."]
    body.extend(f"{key}={values[key]}" for key in ALLOWED_KEYS if values.get(key, "") != "")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temporary:
        temporary.write("\n".join(body) + "\n")
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, 0o644)
    temporary_path.replace(path)


def command_build(arguments: argparse.Namespace) -> int:
    target = target_config(arguments.target)
    return subprocess.run(
        [sys.executable, str(COMMON_DIR / "build_common.py"), "--config", target["build_config"]],
        check=False,
    ).returncode


def command_configure(arguments: argparse.Namespace) -> int:
    target = target_config(arguments.target)
    path = Path(arguments.config)
    values = read_device_config(path)
    values["ZJ_DEVICE"] = arguments.device or target["device"]
    values["ROBOT_TYPE"] = arguments.robot_type
    for key, argument_name in (
        ("ROBOT_NAME", "robot_name"),
        ("ZJ_VERSION", "version"),
        ("ROS_DOMAIN_ID", "ros_domain_id"),
        ("CYCLONEDDS_URI", "cyclonedds_uri"),
        ("COMPOSE_PROFILES", "compose_profiles"),
    ):
        supplied = getattr(arguments, argument_name)
        if supplied is not None:
            values[key] = str(supplied)
    if values["ZJ_DEVICE"] in {"ORIN", "RDK"} and not values.get("COMPOSE_PROFILES"):
        values["COMPOSE_PROFILES"] = robot_types()[arguments.robot_type]["compose_profile"]
    write_device_config(path, values)
    print(f"Wrote {path}")
    return 0


def command_validate_config(arguments: argparse.Namespace) -> int:
    target = target_config(arguments.target)
    path = Path(arguments.config)
    values = read_device_config(path)
    validate_device_config(values)
    if values["ZJ_DEVICE"] != target["device"]:
        raise ValueError(
            f"{path}: ZJ_DEVICE={values['ZJ_DEVICE']} does not match target {arguments.target} ({target['device']})"
        )
    print(f"Validated {path}: {values['ZJ_DEVICE']} {values['ROBOT_TYPE']}")
    return 0


def command_show_targets(_: argparse.Namespace) -> int:
    for name, target in sorted(targets().items()):
        system = "RDK OS" if target["device"] == "RDK" else "Ubuntu"
        print(f"{name}: {target['device']} {system} {target['os_version']} {target['architecture']} ROS 2 {target['ros_distro']}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    target_names = sorted(targets())

    build = commands.add_parser("build", help="build a target common artifact")
    build.add_argument("--target", required=True, choices=target_names)
    build.set_defaults(function=command_build)

    configure = commands.add_parser("configure", help="write a validated device.env")
    configure.add_argument("--target", required=True, choices=target_names)
    configure.add_argument("--robot-type", required=True, choices=sorted(robot_types()))
    configure.add_argument("--config", default=str(DEFAULT_DEVICE_CONFIG))
    configure.add_argument("--device", choices=("ORIN", "PICO", "RDK"))
    configure.add_argument("--robot-name")
    configure.add_argument("--version")
    configure.add_argument("--ros-domain-id", type=int)
    configure.add_argument("--cyclonedds-uri")
    configure.add_argument("--compose-profiles")
    configure.set_defaults(function=command_configure)

    validate = commands.add_parser("validate-config", help="validate an existing device.env for a target")
    validate.add_argument("--target", required=True, choices=target_names)
    validate.add_argument("--config", default=str(DEFAULT_DEVICE_CONFIG))
    validate.set_defaults(function=command_validate_config)

    show_targets = commands.add_parser("show-targets", help="list supported targets")
    show_targets.set_defaults(function=command_show_targets)
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.function(arguments)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
