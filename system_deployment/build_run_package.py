#!/usr/bin/env python3
"""Build an ORIN/PICO/RDK self-extracting Middleware ``.run`` package."""

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROJECT_ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1
CHUNK_SIZE = 1024 * 1024
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]*$")
ENVIRONMENT_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PLATFORMS = ("ORIN", "PICO", "RDK")
ENVIRONMENT_PATHS = {
    "ORIN": "/etc/naviai/Middleware.env",
    "PICO": "/etc/nav01/Middleware.env",
    "RDK": "/etc/naviai/Middleware.env",
}
STARTUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")
SYSTEMD_RESTART_POLICIES = {"no", "on-success", "on-failure", "on-abnormal", "on-watchdog", "always"}


class PackageBuildError(RuntimeError):
    """Raised for invalid manifests and failed package builds."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSON/JSONC manifest, relative to this project or absolute")
    parser.add_argument(
        "--output-dir",
        default="../dist",
        help="directory for the generated .run file (default: projects/dist)",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan without downloading")
    return parser.parse_args()


def resolve_cli_path(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments without touching quoted JSON strings."""
    output: List[str] = []
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            output.append(char)
            if char == "\\" and index + 1 < len(text):
                index += 1
                output.append(text[index])
            elif char == quote:
                quote = ""
        elif char in ('"', "'"):
            quote = char
            output.append(char)
        elif char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        elif char == "/" and following == "*":
            end = text.find("*/", index + 2)
            if end < 0:
                raise PackageBuildError("unterminated JSONC block comment")
            index = end + 2
            continue
        else:
            output.append(char)
        index += 1
    return "".join(output)


def load_jsonc(path: Path) -> Dict[str, Any]:
    try:
        text = strip_json_comments(path.read_text(encoding="utf-8"))
        text = re.sub(r",\s*([}\]])", r"\1", text)
        data = json.loads(text)
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageBuildError("cannot read manifest {}: {}".format(path, exc)) from exc
    if not isinstance(data, dict):
        raise PackageBuildError("manifest root must be an object")
    return data


def require_string(value: Any, field: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise PackageBuildError("{} must be {}string".format(field, "a non-empty " if not allow_empty else "a "))
    return value


def ensure_relative_path(value: Any, field: str) -> PurePosixPath:
    text = require_string(value, field)
    path = PurePosixPath(text)
    if "\\" in text or path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise PackageBuildError("{} must be a safe relative path: {!r}".format(field, text))
    return path


def ensure_absolute_path(value: Any, field: str) -> str:
    text = require_string(value, field)
    path = PurePosixPath(text)
    if not path.is_absolute() or ".." in path.parts:
        raise PackageBuildError("{} must be an absolute traversal-free path: {!r}".format(field, text))
    return str(path)


def resolve_manifest_file(manifest_dir: Path, value: Any, field: str) -> Path:
    relative = ensure_relative_path(value, field)
    candidate = (manifest_dir / Path(*relative.parts)).resolve()
    if candidate != manifest_dir and manifest_dir not in candidate.parents:
        raise PackageBuildError("{} escapes the manifest directory".format(field))
    if not candidate.is_file():
        raise PackageBuildError("{} is not a file: {}".format(field, candidate))
    return candidate


def validate_url(value: str, field: str) -> None:
    if value.startswith("local://"):
        return
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PackageBuildError("{} must use http://, https://, or local://".format(field))


def local_url_source(manifest_dir: Path, value: str, field: str) -> Optional[Path]:
    if not value.startswith("local://"):
        return None
    return resolve_manifest_file(manifest_dir, value[len("local://"):], field)


def validate_checksum(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise PackageBuildError("{} must be a 64-character SHA256 checksum".format(field))
    return value


def safe_token(value: Any, field: str) -> str:
    text = require_string(value, field)
    if not TOKEN_RE.fullmatch(text):
        raise PackageBuildError("{} contains unsupported characters".format(field))
    return text


def normalise_build_metadata(data: Dict[str, Any]) -> Dict[str, str]:
    version = safe_token(data.get("version"), "manifest.version")
    return {
        "version": version,
        "build_time": require_string(data.get("build_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "manifest.build_time"),
        "branch_name": require_string(data.get("branch_name") or "unknown", "manifest.branch_name"),
        "commit_id": require_string(data.get("commit_id") or "unknown", "manifest.commit_id"),
    }


def stage_path(platform: str, category: str, name: str) -> PurePosixPath:
    return PurePosixPath(platform, category, name)


def module_specs(platform: str, device: Dict[str, Any], manifest_dir: Path) -> List[Dict[str, Any]]:
    modules = device.get("modules", [])
    if not isinstance(modules, list):
        raise PackageBuildError("{}.modules must be a list".format(platform))
    result = []
    names = set()
    for index, item in enumerate(modules):
        field = "{}.modules[{}]".format(platform, index)
        allowed = {"name", "version", "url", "image", "dst", "dependencies", "sha256"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise PackageBuildError("{} has unsupported keys".format(field))
        name = safe_token(item.get("name"), field + ".name")
        if name in names:
            raise PackageBuildError("{}.name is duplicated: {}".format(field, name))
        names.add(name)
        url = require_string(item.get("url", ""), field + ".url", allow_empty=True)
        image = require_string(item.get("image", ""), field + ".image", allow_empty=True)
        if bool(url) == bool(image):
            raise PackageBuildError("{} must configure exactly one of url or image".format(field))
        version = require_string(item.get("version", ""), field + ".version", allow_empty=True)
        dependencies = item.get("dependencies", [])
        if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
            raise PackageBuildError("{}.dependencies must be a string list".format(field))
        if image:
            result.append({"name": name, "version": version, "image": image, "dependencies": dependencies, "kind": "image"})
            continue
        validate_url(url, field + ".url")
        local_url_source(manifest_dir, url, field + ".url")
        dst = item.get("dst") or ""
        destination_dir = ensure_relative_path(dst, field + ".dst") if dst else None
        destination_parts = (platform, ".dists", *(destination_dir.parts if destination_dir else ()), "{:03d}-{}.deb".format(index, name))
        destination = PurePosixPath(*destination_parts)
        result.append({"name": name, "version": version, "url": url, "sha256": validate_checksum(item.get("sha256"), field + ".sha256"), "destination": destination, "dependencies": dependencies, "kind": "deb"})
    return result


def resource_specs(platform: str, device: Dict[str, Any], manifest_dir: Path) -> List[Dict[str, Any]]:
    resources = device.get("resource", device.get("resources", []))
    if not isinstance(resources, list):
        raise PackageBuildError("{}.resource must be a list".format(platform))
    result = []
    for index, item in enumerate(resources):
        field = "{}.resource[{}]".format(platform, index)
        allowed = {"url", "local_path", "path", "device_path", "sha256"}
        if not isinstance(item, dict) or set(item) - allowed:
            raise PackageBuildError("{} has unsupported keys".format(field))
        url = require_string(item.get("url", ""), field + ".url", allow_empty=True)
        local_path = require_string(item.get("local_path", ""), field + ".local_path", allow_empty=True)
        if bool(url) == bool(local_path):
            raise PackageBuildError("{} must configure exactly one of url or local_path".format(field))
        target_key = "path" if platform in {"ORIN", "RDK"} else "device_path"
        device_path = ensure_absolute_path(item.get(target_key), field + "." + target_key)
        if url:
            validate_url(url, field + ".url")
            source_url = url
            local_source = local_url_source(manifest_dir, url, field + ".url")
        else:
            local_source = resolve_manifest_file(manifest_dir, local_path, field + ".local_path")
            source_url = "local://{}".format(local_path)
        source_name = local_source.name if local_source is not None else Path(urllib.parse.urlparse(source_url).path).name
        if not source_name:
            source_name = "resource"
        destination = stage_path(platform, "resources", "{:03d}-{}".format(index, safe_token(source_name, field + ".filename")))
        result.append({"url": source_url, "local_source": local_source, "destination": destination, "device_path": device_path, "sha256": validate_checksum(item.get("sha256"), field + ".sha256")})
    return result


def hook_specs(platform: str, device: Dict[str, Any], manifest_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    scripts = device.get("scripts", {})
    hook_names = ("pre_install", "post_install", "pre_uninstall", "post_uninstall")
    if not isinstance(scripts, dict) or set(scripts) - set(hook_names):
        raise PackageBuildError("{}.scripts supports only install/uninstall hook lists".format(platform))
    result: Dict[str, List[Dict[str, Any]]] = {name: [] for name in hook_names}
    for hook_name in hook_names:
        hooks = scripts.get(hook_name, [])
        if not isinstance(hooks, list):
            raise PackageBuildError("{}.scripts.{} must be a list".format(platform, hook_name))
        for index, item in enumerate(hooks):
            field = "{}.scripts.{}[{}]".format(platform, hook_name, index)
            if not isinstance(item, dict) or set(item) - {"name", "cmd", "path"}:
                raise PackageBuildError("{} supports only name, cmd and path".format(field))
            name = require_string(item.get("name") or "{}-{}".format(hook_name, index), field + ".name")
            cmd = require_string(item.get("cmd", ""), field + ".cmd", allow_empty=True)
            path = require_string(item.get("path", ""), field + ".path", allow_empty=True)
            if bool(cmd) == bool(path):
                raise PackageBuildError("{} must configure exactly one of cmd or path".format(field))
            spec: Dict[str, Any] = {"name": name}
            if cmd:
                spec["cmd"] = cmd
            else:
                source = resolve_manifest_file(manifest_dir, path, field + ".path")
                destination = stage_path(platform, "hooks/{}".format(hook_name), "{:03d}-{}".format(index, safe_token(source.name, field + ".filename")))
                spec.update({"source": source, "destination": destination})
            result[hook_name].append(spec)
    return result


def environment_specs(platform: str, device: Dict[str, Any]) -> Dict[str, str]:
    """Validate optional module settings written into Middleware.env.

    The carrier owns the platform, ROS and device bootstrap.  A run package
    may add module-specific settings (for example a chassis endpoint) but may
    not replace the generated MIDDLEWARE_* bookkeeping values.
    """
    environment = device.get("environment", {})
    if not isinstance(environment, dict):
        raise PackageBuildError("{}.environment must be an object".format(platform))
    result: Dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not ENVIRONMENT_KEY_RE.fullmatch(key):
            raise PackageBuildError("{}.environment keys must be shell variable names".format(platform))
        if key.startswith("MIDDLEWARE_"):
            raise PackageBuildError("{}.environment may not override {}".format(platform, key))
        result[key] = require_string(value, "{}.environment.{}".format(platform, key), allow_empty=True)
    return result


def startup_spec(platform: str, device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate one declarative systemd startup entry for a platform."""
    startup = device.get("startup")
    if startup is None:
        return None
    field = "{}.startup".format(platform)
    allowed = {"name", "description", "command", "script_directory", "restart", "start_on_install"}
    if not isinstance(startup, dict) or set(startup) - allowed:
        raise PackageBuildError("{}.startup supports only {}".format(field, ", ".join(sorted(allowed))))
    name = require_string(startup.get("name"), field + ".name")
    if not STARTUP_NAME_RE.fullmatch(name) or name.endswith(".service"):
        raise PackageBuildError("{}.name must be a systemd unit basename without .service".format(field))
    command = require_string(startup.get("command"), field + ".command")
    if "\n" in command or "\r" in command:
        raise PackageBuildError("{}.command must be a single line".format(field))
    description = require_string(startup.get("description") or "{} service".format(name), field + ".description")
    if "\n" in description or "\r" in description:
        raise PackageBuildError("{}.description must be a single line".format(field))
    script_directory = ensure_absolute_path(startup.get("script_directory") or "{}/{}".format(PurePosixPath(ENVIRONMENT_PATHS[platform]).parent, name), field + ".script_directory")
    restart = require_string(startup.get("restart") or "on-failure", field + ".restart")
    if restart not in SYSTEMD_RESTART_POLICIES:
        raise PackageBuildError("{}.restart must be one of {}".format(field, ", ".join(sorted(SYSTEMD_RESTART_POLICIES))))
    start_on_install = startup.get("start_on_install", True)
    if not isinstance(start_on_install, bool):
        raise PackageBuildError("{}.start_on_install must be true or false".format(field))
    script_path = str(PurePosixPath(script_directory, "{}-start.sh".format(name)))
    return {
        "platform": platform,
        "name": name,
        "description": description,
        "command": command,
        "script_path": script_path,
        "service_path": "/etc/systemd/system/{}.service".format(name),
        "restart": restart,
        "start_on_install": start_on_install,
        "script_destination": stage_path(platform, "startup", "{}-start.sh".format(name)),
        "service_destination": stage_path(platform, "startup", "{}.service".format(name)),
    }


def device_spec(platform: str, raw: Any, manifest_dir: Path, build: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PackageBuildError("{} must be an object".format(platform))
    allowed = {"sys_env_version", "build_time", "branch_name", "commit_id", "modules", "resource", "resources", "zjhrobot", "scripts", "environment", "startup"}
    if set(raw) - allowed:
        raise PackageBuildError("{} has unsupported keys".format(platform))
    if "resource" in raw and "resources" in raw:
        raise PackageBuildError("{} may use resource or resources, not both".format(platform))
    for field in ("sys_env_version", "build_time", "branch_name", "commit_id"):
        if field in raw:
            require_string(raw[field], "{}.{}".format(platform, field), allow_empty=True)
    if "zjhrobot" in raw and not isinstance(raw["zjhrobot"], dict):
        raise PackageBuildError("{}.zjhrobot must be an object".format(platform))
    return {"platform": platform, "sys_env_version": raw.get("sys_env_version", ""), "build_time": raw.get("build_time") or build["build_time"], "branch_name": raw.get("branch_name") or build["branch_name"], "commit_id": raw.get("commit_id") or build["commit_id"], "modules": module_specs(platform, raw, manifest_dir), "resources": resource_specs(platform, raw, manifest_dir), "hooks": hook_specs(platform, raw, manifest_dir), "environment": environment_specs(platform, raw), "startup": startup_spec(platform, raw), "zjhrobot": raw.get("zjhrobot", {})}


def load_manifest(path: Path) -> Dict[str, Any]:
    data = load_jsonc(path)
    allowed = {"schema_version", "version", "build_time", "branch_name", "commit_id", "ORIN", "PICO", "RDK", "output_name"}
    unknown = set(data) - allowed
    if unknown:
        raise PackageBuildError("manifest has unsupported keys: {}".format(", ".join(sorted(unknown))))
    if data.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise PackageBuildError("manifest schema_version must be {}".format(SCHEMA_VERSION))
    build = normalise_build_metadata(data)
    manifest_dir = path.parent.resolve()
    devices = [device_spec(platform, data.get(platform), manifest_dir, build) for platform in PLATFORMS]
    devices = [device for device in devices if device is not None]
    if not devices:
        raise PackageBuildError("manifest must contain ORIN, PICO, and/or RDK")
    output_name = data.get("output_name")
    if output_name is not None:
        safe_token(output_name, "manifest.output_name")
    return {"build": build, "devices": devices, "output_name": output_name}


def output_filename(manifest: Dict[str, Any]) -> str:
    if manifest["output_name"]:
        return "{}.run".format(manifest["output_name"])
    build = manifest["build"]
    date = re.sub(r"\D", "", build["build_time"])[:8] or "unknown"
    branch = re.sub(r"[^A-Za-z0-9._+-]", "_", build["branch_name"])
    commit = re.sub(r"[^A-Za-z0-9._+-]", "_", build["commit_id"])[:8]
    return "Middleware_{}_{}_{}_{}.run".format(build["version"], branch, commit, date)


def platform_delivery_target(platform: str, sys_env_version: str) -> Dict[str, str]:
    ros_distro = "humble" if "20.04" in sys_env_version or "22.04" in sys_env_version else "jazzy"
    if platform == "PICO":
        return {"arch": "amd64", "os": "ubuntu", "os_version": "20.04", "ros_distro": ros_distro}
    if platform == "RDK":
        return {"arch": "arm64", "os": "rdk", "os_version": "V5.1.0", "ros_distro": "jazzy"}
    ubuntu_prefix = "ubuntu-"
    os_version = sys_env_version[len(ubuntu_prefix):] if sys_env_version.startswith(ubuntu_prefix) else sys_env_version
    return {"arch": "arm64", "os": "ubuntu", "os_version": os_version or "unknown", "ros_distro": ros_distro}


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def delivery_yaml(build: Dict[str, str], device: Dict[str, Any], output_name: Optional[str]) -> str:
    target = platform_delivery_target(device["platform"], device["sys_env_version"])
    name = output_name or "middleware-{}".format(device["platform"].lower())
    startup = device["startup"]
    description = startup["description"] if startup is not None else "Navi Middleware delivery"
    common_package = {"ORIN": "navi-common-dep", "PICO": "navi-pico-common-dep", "RDK": "navi-rdk-common-dep"}[device["platform"]]
    services = ["{}.service".format(startup["name"])] if startup is not None else []
    debs = [module["name"] for module in device["modules"] if module["kind"] == "deb"]
    env_path = ENVIRONMENT_PATHS[device["platform"]]
    lines = [
        "apiVersion: robot-studio/v1",
        "kind: Delivery",
        "metadata:",
        "  name: {}".format(name),
        "  version: {}".format(build["version"]),
        "spec:",
        "  description: {}".format(yaml_quote(description)),
        "  target:",
        "    arch: {}".format(target["arch"]),
        "    os: {}".format(target["os"]),
        "    os_version: {}".format(yaml_quote(target["os_version"])),
        "    ros_distro: {}".format(target["ros_distro"]),
        "  requires:",
        "    packages:",
        "      - {name: %s, min_version: %s}" % (common_package, yaml_quote("2.0.0")),
        "    runtime:",
        "      - {path: %s, reason: %s}" % (env_path, yaml_quote("platform ROS 2 environment")),
        "  contents:",
        "    debs: [{}]".format(", ".join(debs)),
        "  install:",
        "    entry: ./install.sh",
        "    needs_root: true",
        "  runtime:",
        "    services: [{}]".format(", ".join(services)),
        "    dds: {domain_id: 72, rmw: rmw_cyclonedds_cpp}",
        "",
    ]
    return "\n".join(lines)


def packages_tsv(device: Dict[str, Any]) -> str:
    return "".join("{}\t{}\t{}\n".format(module["name"], module["version"], module["kind"]) for module in device["modules"])


def robot_types_tsv() -> str:
    source = PROJECT_ROOT / "common/configs/robot-types.json"
    try:
        data = json.loads(source.read_text(encoding="utf-8"))["robot_types"]
    except (OSError, KeyError, json.JSONDecodeError):
        return ""
    return "".join("{}\t{}\n".format(name, value.get("compose_profile", "")) for name, value in sorted(data.items()))


def verify_checksum(path: Path, digest: str, expected: Optional[str]) -> None:
    if expected is not None and digest.lower() != expected.lower():
        path.unlink(missing_ok=True)
        raise PackageBuildError("SHA256 mismatch for {}".format(path.name))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_or_download(source: Optional[Path], url: str, destination: Path, checksum: Optional[str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    try:
        input_file = source.open("rb") if source is not None else urllib.request.urlopen(url)
        with input_file, destination.open("wb") as output_file:
            while True:
                chunk = input_file.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                output_file.write(chunk)
    except OSError as exc:
        raise PackageBuildError("cannot retrieve {}: {}".format(url, exc)) from exc
    verify_checksum(destination, digest.hexdigest(), checksum)


def copy_to_stage(source: Path, stage_root: Path, destination: PurePosixPath) -> None:
    target = stage_root.joinpath(*destination.parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(target.stat().st_mode | 0o111)


def environment_lines(build: Dict[str, str], device: Dict[str, Any]) -> List[str]:
    modules = ",".join("{}={}".format(module["name"], module["version"]) for module in device["modules"])

    def item(key: str, value: str) -> str:
        return "export {}={}".format(key, shlex.quote(value))

    # A run package must not overwrite the carrier's shared device identity or
    # ROS runtime bootstrap with metadata-only environment variables.
    nounset_prologue = [
        "# ROS setup scripts read optional variables directly.",
        "_middleware_restore_nounset=0",
        "case \"$-\" in",
        "    *u*) _middleware_restore_nounset=1; set +u ;;",
        "esac",
        "",
    ]
    nounset_epilogue = [
        "if [ \"${_middleware_restore_nounset}\" -eq 1 ]; then",
        "    set -u",
        "fi",
        "unset _middleware_restore_nounset",
        "",
    ]

    device_profile = [
        "# Load validated device identity and shared DDS settings when available.",
        "if [ -r /etc/profile.d/zj_humanoid.sh ]; then",
        "    _middleware_env_only_previous=\"${ZJ_PROFILE_ENV_ONLY-__MIDDLEWARE_UNSET__}\"",
        "    export ZJ_PROFILE_ENV_ONLY=1",
        "    if ! . /etc/profile.d/zj_humanoid.sh; then",
        "        echo \"Middleware environment: device profile is not configured.\" >&2",
        "    fi",
        "    if [ \"${_middleware_env_only_previous}\" = \"__MIDDLEWARE_UNSET__\" ]; then",
        "        unset ZJ_PROFILE_ENV_ONLY",
        "    else",
        "        export ZJ_PROFILE_ENV_ONLY=\"${_middleware_env_only_previous}\"",
        "    fi",
        "    unset _middleware_env_only_previous",
        "fi",
        "",
    ]

    if device["platform"] == "ORIN":
        runtime_bootstrap = [
            "# Load the ROS runtime selected by the installed Orin OS.",
            "_middleware_os_version=\"\"",
            "if [ -r /etc/os-release ]; then",
            "    . /etc/os-release",
            "    _middleware_os_version=\"${VERSION_ID:-}\"",
            "fi",
            "case \"${_middleware_os_version}\" in",
            "    22.04) export MIDDLEWARE_ROS_DISTRO=\"humble\" ;;",
            "    24.04) export MIDDLEWARE_ROS_DISTRO=\"jazzy\" ;;",
            "    *) export MIDDLEWARE_ROS_DISTRO=\"\" ;;",
            "esac",
            "if [ -n \"${MIDDLEWARE_ROS_DISTRO}\" ] && [ -r \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\" ]; then",
            "    . \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\"",
            "fi",
            "unset _middleware_os_version",
            "",
        ]
    elif device["platform"] == "PICO":
        runtime_bootstrap = [
            "# Pico packages use the ROS 2 Humble runtime.",
            "export MIDDLEWARE_ROS_DISTRO=\"humble\"",
            "if [ -r \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\" ]; then",
            "    . \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\"",
            "fi",
            "",
        ]
    else:
        runtime_bootstrap = [
            "# RDK OS V5.1.0 uses the ROS 2 Jazzy/Noble resolver contract.",
            "export MIDDLEWARE_ROS_DISTRO=\"jazzy\"",
            "export ROSDEP_OS_OVERRIDE=\"${ROSDEP_OS_OVERRIDE:-ubuntu:noble}\"",
            "export ROS_OS_OVERRIDE=\"${ROS_OS_OVERRIDE:-${ROSDEP_OS_OVERRIDE}:noble}\"",
            "if [ -z \"${ROSDISTRO_INDEX_URL:-}\" ] && [ -r /opt/rosdistro/index-v4.yaml ]; then",
            "    export ROSDISTRO_INDEX_URL=\"file:///opt/rosdistro/index-v4.yaml\"",
            "fi",
            "if [ -r \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\" ]; then",
            "    . \"/opt/ros/${MIDDLEWARE_ROS_DISTRO}/setup.bash\"",
            "fi",
            "",
        ]

    custom_environment = [item(key, value) for key, value in sorted(device["environment"].items())]
    if custom_environment:
        custom_environment.insert(0, "# Module-specific settings configured by the run manifest.")

    return nounset_prologue + device_profile + runtime_bootstrap + nounset_epilogue + custom_environment + [
        "# Managed by Middleware run package. Source this file before starting modules.",
        item("MIDDLEWARE_PLATFORM", device["platform"]), item("MIDDLEWARE_VERSION", build["version"]),
        item("MIDDLEWARE_BUILD_TIME", device["build_time"]), item("MIDDLEWARE_BRANCH_NAME", device["branch_name"]),
        item("MIDDLEWARE_COMMIT_ID", device["commit_id"]), item("MIDDLEWARE_SYS_ENV_VERSION", device["sys_env_version"]),
        item("MIDDLEWARE_MODULES", modules), item("MIDDLEWARE_PACKAGE_ID", "{}:{}:{}".format(build["version"], device["platform"], device["commit_id"])),
    ]


def render_startup_script(startup: Dict[str, Any], env_path: str) -> str:
    return "\n".join([
        "#!/bin/bash",
        "# Generated by Middleware run package; do not edit this generated file.",
        "set -e",
        ". {}".format(shlex.quote(env_path)),
        "exec /bin/bash -c {}".format(shlex.quote(startup["command"])),
        "",
    ])


def render_startup_service(startup: Dict[str, Any]) -> str:
    state_directory = "/var/lib/naviai/{}".format(startup["name"])
    log_directory = "/var/log/naviai/{}".format(startup["name"])
    return "\n".join([
        "[Unit]",
        "Description={}".format(startup["description"]),
        "After=network-online.target",
        "Wants=network-online.target",
        "ConditionPathExists={}".format(ENVIRONMENT_PATHS[startup["platform"]]),
        "",
        "[Service]",
        "Type=simple",
        "Environment=HOME=/root",
        "Environment=ROS_HOME={}".format(state_directory),
        "Environment=ROS_LOG_DIR={}".format(log_directory),
        "ExecStartPre=/usr/bin/install -d -m 0755 {} {}".format(state_directory, log_directory),
        "ExecStart={}".format(startup["script_path"]),
        "Restart={}".format(startup["restart"]),
        "RestartSec=3",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ])


def render_hook_lines(device: Dict[str, Any], hook: str) -> List[str]:
    lines = []
    for item in device["hooks"][hook]:
        lines.append("echo {}".format(shlex.quote("Middleware {}: {}".format(hook, item["name"]))))
        lines.append("sh -c {}".format(shlex.quote(item["cmd"]))) if "cmd" in item else lines.append('"$package_root/{}"'.format(item["destination"]))
    return lines


def render_device_script(build: Dict[str, str], device: Dict[str, Any]) -> str:
    platform = device["platform"]
    env_path = ENVIRONMENT_PATHS[platform]
    expected = "export MIDDLEWARE_PACKAGE_ID={}".format(shlex.quote("{}:{}:{}".format(build["version"], platform, device["commit_id"])))
    package_id = "{}:{}:{}".format(build["version"], platform, device["commit_id"])
    receipt_path = "/var/lib/naviai/deliveries/{}-{}-{}.receipt".format(platform.lower(), build["version"], device["commit_id"])
    packages_path = "$package_root/{}/packages.tsv".format(platform)
    environment_write = ["    env_dir={}".format(shlex.quote(str(PurePosixPath(env_path).parent))), "    mkdir -p \"$env_dir\"", "    env_tmp=$(mktemp \"$env_dir/.Middleware.env.XXXXXX\")", "    {"]
    for line in environment_lines(build, device):
        environment_write.append("        printf '%s\\n' {}".format(shlex.quote(line)))
    environment_write.extend(["    } > \"$env_tmp\"", "    chmod 0644 \"$env_tmp\"", "    mv \"$env_tmp\" {}".format(shlex.quote(env_path))])

    lines = [
        "#!/bin/sh", "set -eu", 'action="${1:-install}"', 'robot_type="${2:-}"', 'force="${3:-}"',
        'if [ "$(id -u)" -ne 0 ] && [ "$action" != "--status" ] && [ "$action" != "--verify-only" ]; then echo "ERROR: run with sudo" >&2; exit 1; fi',
        'package_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)',
        'packages_file=' + packages_path,
        'receipt_path=' + shlex.quote(receipt_path),
        'expected_receipt=' + shlex.quote(package_id),
        'tab=$(printf "\\t")',
        'check_packages() {',
        '    while IFS="	" read -r package_name package_version package_kind; do',
        '        [ "$package_kind" = deb ] || continue',
        '        installed=$(dpkg-query -W -f="${Version}\t${Status}" "$package_name" 2>/dev/null || true)',
        '        [ "$installed" = "${package_version}${tab}install ok installed" ] || { echo "ERROR: $package_name expected $package_version, got ${installed:-not-installed}" >&2; return 1; }',
        '    done < "$packages_file"',
        '}',
        'check_runtime() {',
        '    [ -r ' + shlex.quote(env_path) + ' ] || { echo "ERROR: Middleware environment is missing: ' + env_path + '" >&2; return 1; }',
        '    /bin/bash -c ". ' + shlex.quote(env_path) + '"',
        '}',
        'case "$action" in',
        '  --status) if [ -r "$receipt_path" ] && [ "$(cat \"$receipt_path\")" = "$expected_receipt" ]; then echo "Delivery receipt: matched"; else echo "Delivery receipt: missing or mismatched"; fi; check_packages; check_runtime; exit $? ;;',
        '  --verify-only) check_packages; check_runtime; exit 0 ;;',
        '  install|uninstall) ;;',
        '  *) echo "ERROR: unsupported action: $action" >&2; exit 2 ;;',
        'esac',
        "",
        'if [ "$action" = install ]; then',
    ]
    if device["startup"] is not None:
        lines[lines.index('  --status) if [ -r "$receipt_path" ] && [ "$(cat \"$receipt_path\")" = "$expected_receipt" ]; then echo "Delivery receipt: matched"; else echo "Delivery receipt: missing or mismatched"; fi; check_packages; check_runtime; exit $? ;;')] = '  --status) if [ -r "$receipt_path" ] && [ "$(cat \"$receipt_path\")" = "$expected_receipt" ]; then echo "Delivery receipt: matched"; else echo "Delivery receipt: missing or mismatched"; fi; check_packages; check_runtime; echo "Service enabled:"; systemctl is-enabled {}.service || true; echo "Service active:"; systemctl is-active {}.service || true; exit 0 ;;'.format(device["startup"]["name"], device["startup"]["name"])
        lines[lines.index('  --verify-only) check_packages; check_runtime; exit 0 ;;')] = '  --verify-only) check_packages; check_runtime; systemctl is-active --quiet {}.service || {{ echo "ERROR: {}.service is not active" >&2; exit 1; }}; exit 0 ;;'.format(device["startup"]["name"], device["startup"]["name"])
    # Some module postinst scripts source this file, so it must exist before
    # the first hook or dpkg transaction begins.
    lines.extend([
        '    if [ -n "$robot_type" ]; then',
        '        robot_record=$(grep -F "$robot_type	" "$package_root/robot-types.tsv" || true)',
        '        [ -n "$robot_record" ] || { echo "ERROR: unsupported robot type: $robot_type" >&2; exit 2; }',
        '        robot_profile=$(printf "%s" "$robot_record" | cut -f2)',
        '        device_config=/etc/zj_humanoid/device.env',
        '        mkdir -p /etc/zj_humanoid',
        '        device_tmp=$(mktemp /etc/zj_humanoid/.device.env.XXXXXX)',
        '        [ -r "$device_config" ] && grep -Ev "^(ZJ_DEVICE|ROBOT_TYPE|COMPOSE_PROFILES)=" "$device_config" > "$device_tmp" || :',
        '        printf "ZJ_DEVICE=%s\\nROBOT_TYPE=%s\\nCOMPOSE_PROFILES=%s\\n" ' + shlex.quote(platform) + ' "$robot_type" "$robot_profile" >> "$device_tmp"',
        '        chmod 0644 "$device_tmp"; mv "$device_tmp" "$device_config"',
        '    fi',
    ])
    lines.extend(environment_write)
    lines.extend("    " + line for line in render_hook_lines(device, "pre_install"))
    for module in device["modules"]:
        lines.append('    dpkg -i "$package_root/{}"'.format(module["destination"])) if module["kind"] == "deb" else lines.append("    docker pull {}".format(shlex.quote(module["image"])))
    for resource in device["resources"]:
        lines.append('    install -D -m 0644 "$package_root/{}" {}'.format(resource["destination"], shlex.quote(resource["device_path"])))
    lines.extend("    " + line for line in render_hook_lines(device, "post_install"))
    startup = device["startup"]
    if startup is not None:
        lines.extend([
            '    install -D -m 0755 "$package_root/{}" {}'.format(startup["script_destination"], shlex.quote(startup["script_path"])),
            '    install -D -m 0644 "$package_root/{}" {}'.format(startup["service_destination"], shlex.quote(startup["service_path"])),
            "    systemctl daemon-reload",
        ])
        if startup["start_on_install"]:
            # ``enable --now`` leaves an already-active unit untouched.  The
            # run package may just have replaced its start script, so restart
            # it to ensure the deployed command takes effect on upgrades too.
            lines.extend([
                "    systemctl enable {}.service".format(shlex.quote(startup["name"])),
                "    systemctl restart {}.service".format(shlex.quote(startup["name"])),
            ])
    lines.extend([
        '    mkdir -p "$(dirname \"$receipt_path\")"',
        '    receipt_tmp=$(mktemp "$(dirname \"$receipt_path\")/.receipt.XXXXXX")',
        '    printf "%s\\n" "$expected_receipt" > "$receipt_tmp"',
        '    chmod 0644 "$receipt_tmp"; mv "$receipt_tmp" "$receipt_path"',
    ])
    lines.extend(["    exit 0", "fi", ""])
    lines.extend([
        'if [ -r "$receipt_path" ]; then',
        '    [ "$(cat \"$receipt_path\")" = "$expected_receipt" ] || { echo "ERROR: installation receipt belongs to a different delivery" >&2; exit 1; }',
        'elif [ "$force" != "--force" ]; then',
        '    echo "ERROR: installation receipt is missing; use --force only for this case" >&2; exit 1',
        'fi',
        'check_packages',
    ])
    if startup is not None:
        lines.append("systemctl disable --now {}.service || true".format(shlex.quote(startup["name"])))
    lines.extend(render_hook_lines(device, "pre_uninstall"))
    for module in reversed(device["modules"]):
        if module["kind"] == "deb":
            lines.append("dpkg -r {}".format(shlex.quote(module["name"])))
    lines.extend(["if [ -f {} ] && grep -Fqx {} {}; then".format(shlex.quote(env_path), shlex.quote(expected), shlex.quote(env_path)), "    rm -f {}".format(shlex.quote(env_path)), "fi"])
    if startup is not None:
        lines.extend([
            "rm -f {} {}".format(shlex.quote(startup["script_path"]), shlex.quote(startup["service_path"])),
            "systemctl daemon-reload",
        ])
    lines.append('rm -f "$receipt_path"')
    lines.extend(render_hook_lines(device, "post_uninstall"))
    return "\n".join(lines) + "\n"


def render_launcher(devices: Sequence[Dict[str, Any]], build: Dict[str, str]) -> str:
    available = {device["platform"] for device in devices}
    lines = [
        "#!/bin/sh", "set -eu", 'action="install"', 'device=""', 'robot_type=""', 'force=""',
        'usage() {',
        '  echo "Usage: $0 [install|uninstall] [--device ORIN|PICO|RDK] [--robot-type TYPE]"',
        '  echo "       $0 -- --version|--delivery|--packages|--info|--verify|--status|--verify-only|--uninstall [--force]"',
        '  echo "Supported robot types:"; cut -f1 robot-types.tsv | paste -sd " " -',
        '}',
        'while [ "$#" -gt 0 ]; do',
        '    case "$1" in',
        '        --) shift; continue ;;',
        '        install) action="install" ;;',
        '        uninstall|--uninstall) action="uninstall" ;;',
        '        --force) force="--force" ;;',
        '        --robot-type) shift; [ "$#" -gt 0 ] || { echo "ERROR: --robot-type needs a model" >&2; exit 2; }; robot_type="$1" ;;',
        '        --robot-type=*) robot_type="${1#--robot-type=}" ;;',
        '        --device) shift; [ "$#" -gt 0 ] || { echo "ERROR: --device needs ORIN, PICO, or RDK" >&2; exit 2; }; device="$1" ;;',
        '        --device=*) device="${1#--device=}" ;;',
        '        -h|--help) usage; exit 0 ;;',
        '        --version|--delivery|--packages|--info|--verify|--status|--verify-only) action="$1" ;;',
        '        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;',
        '    esac',
        '    shift',
        'done',
        'case "$action" in',
        '  --version) echo "{}"; exit 0 ;;'.format(build["version"]),
        '  --verify) sha256sum -c payloads.sha256; exit $? ;;',
        'esac',
        'if [ -z "$device" ]; then',
    ]
    if available == {"ORIN"}:
        lines.append('    device="ORIN"')
    elif available == {"PICO"}:
        lines.append('    device="PICO"')
    elif available == {"RDK"}:
        lines.append('    device="RDK"')
    else:
        lines.extend(['    if [ -r /etc/os-release ] && grep -Eq \'^ID="?rdk os"?$\' /etc/os-release; then device="RDK"; fi', '    if [ -z "$device" ] && { [ -d /etc/naviai ] || id naviai >/dev/null 2>&1; }; then device="ORIN"; fi', '    if [ -d /etc/nav01 ] || id nav01 >/dev/null 2>&1; then', '        if [ -n "$device" ]; then echo "ERROR: multiple platforms were detected; use --device" >&2; exit 2; fi', '        device="PICO"', '    fi', '    [ -n "$device" ] || { echo "ERROR: cannot detect device; use --device ORIN|PICO|RDK" >&2; exit 2; }'])
    lines.append("fi")
    lines.extend([
        'case "$action" in',
        '  --delivery) cat "$device/delivery.yaml"; exit 0 ;;',
        '  --packages) cat "$device/packages.tsv"; exit 0 ;;',
        '  --info) echo "Version: {}"; cat "$device/delivery.yaml"; echo "Packages:"; cat "$device/packages.tsv"; exit 0 ;;'.format(build["version"]),
        'esac',
        'sha256sum -c payloads.sha256',
    ])
    lines.append('case "$device" in')
    for platform in PLATFORMS:
        if platform in available:
            lines.append("    {}) exec \"$(dirname \"$0\")/{}/run.sh\" \"$action\" \"$robot_type\" \"$force\" ;;".format(platform, platform))
    lines.extend(['    *) echo "ERROR: this package does not contain $device" >&2; exit 2 ;;', "esac"])
    return "\n".join(lines) + "\n"


def run_header() -> bytes:
    template = """#!/bin/sh
# Self-extracting package generated by system_deployment/build_run_package.py.
set -eu
archive_line=__ARCHIVE_LINE__
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/middleware-run.XXXXXX")
cleanup() { rm -rf "$work_dir"; }
trap cleanup EXIT HUP INT TERM
tail -n +"$archive_line" "$0" | tar -xzf - -C "$work_dir"
cd "$work_dir"
./launcher.sh "$@"
status=$?
exit "$status"
__ARCHIVE_BELOW__
"""
    return template.replace("__ARCHIVE_LINE__", str(len(template.splitlines(keepends=True)) + 1)).encode("utf-8")


def create_run_file(stage_root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".middleware-run-", dir=str(output.parent))
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as output_file:
            output_file.write(run_header())
            with tarfile.open(fileobj=output_file, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for source in sorted(stage_root.rglob("*")):
                    if source.is_file():
                        archive.add(source, arcname=source.relative_to(stage_root).as_posix(), recursive=False)
        temporary.chmod(0o755)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def write_payload_checksums(stage_root: Path) -> None:
    entries = []
    for source in sorted(stage_root.rglob("*")):
        if source.is_file() and source.name != "payloads.sha256":
            entries.append("{}  {}\n".format(file_sha256(source), source.relative_to(stage_root).as_posix()))
    (stage_root / "payloads.sha256").write_text("".join(entries), encoding="utf-8")


def build(manifest_path: Path, output_dir: Path, dry_run: bool = False) -> Path:
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent.resolve()
    output = output_dir.resolve() / output_filename(manifest)
    print("Middleware {} -> {}".format(manifest["build"]["version"], output))
    for device in manifest["devices"]:
        print("{}: {} module(s), {} resource(s), {} startup service(s)".format(device["platform"], len(device["modules"]), len(device["resources"]), int(device["startup"] is not None)))
    if dry_run:
        return output
    with tempfile.TemporaryDirectory(prefix="middleware-run-build-") as temporary_dir:
        stage_root = Path(temporary_dir) / "payload"
        stage_root.mkdir()
        for device in manifest["devices"]:
            for module in device["modules"]:
                if module["kind"] == "deb":
                    source = local_url_source(manifest_dir, module["url"], "{}.module.url".format(device["platform"]))
                    copy_or_download(source, module["url"], stage_root.joinpath(*module["destination"].parts), module["sha256"])
            for resource in device["resources"]:
                source = resource["local_source"] or local_url_source(manifest_dir, resource["url"], "{}.resource.url".format(device["platform"]))
                copy_or_download(source, resource["url"], stage_root.joinpath(*resource["destination"].parts), resource["sha256"])
            for hooks in device["hooks"].values():
                for hook in hooks:
                    if "source" in hook:
                        copy_to_stage(hook["source"], stage_root, hook["destination"])
            startup = device["startup"]
            if startup is not None:
                generated_script = stage_root.joinpath(*startup["script_destination"].parts)
                generated_script.parent.mkdir(parents=True, exist_ok=True)
                generated_script.write_text(render_startup_script(startup, ENVIRONMENT_PATHS[device["platform"]]), encoding="utf-8")
                generated_script.chmod(0o755)
                generated_service = stage_root.joinpath(*startup["service_destination"].parts)
                generated_service.parent.mkdir(parents=True, exist_ok=True)
                generated_service.write_text(render_startup_service(startup), encoding="utf-8")
            device_root = stage_root / device["platform"]
            (device_root / "delivery.yaml").write_text(delivery_yaml(manifest["build"], device, manifest["output_name"]), encoding="utf-8")
            (device_root / "packages.tsv").write_text(packages_tsv(device), encoding="utf-8")
            install_entry = device_root / "install.sh"
            install_entry.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/run.sh\" install \"$@\"\n", encoding="utf-8")
            install_entry.chmod(0o755)
            script_path = stage_root / device["platform"] / "run.sh"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(render_device_script(manifest["build"], device), encoding="utf-8")
            script_path.chmod(0o755)
        (stage_root / "launcher.sh").write_text(render_launcher(manifest["devices"], manifest["build"]), encoding="utf-8")
        (stage_root / "launcher.sh").chmod(0o755)
        (stage_root / "robot-types.tsv").write_text(robot_types_tsv(), encoding="utf-8")
        (stage_root / "package-manifest.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "build": manifest["build"], "devices": manifest["devices"]}, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        write_payload_checksums(stage_root)
        create_run_file(stage_root, output)
    return output


def main() -> int:
    args = parse_args()
    try:
        output = build(resolve_cli_path(args.manifest), resolve_cli_path(args.output_dir), args.dry_run)
        print("PASS: {}{}".format("validated " if args.dry_run else "created ", output))
        return 0
    except (PackageBuildError, OSError, tarfile.TarError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
