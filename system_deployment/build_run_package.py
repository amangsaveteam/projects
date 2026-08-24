#!/usr/bin/env python3
"""Build an ORIN/PICO self-extracting Middleware ``.run`` package."""

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
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
PLATFORMS = ("ORIN", "PICO")
ENVIRONMENT_PATHS = {"ORIN": "/etc/naviai/Middleware.env", "PICO": "/etc/nav01/Middleware.env"}


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
        target_key = "path" if platform == "ORIN" else "device_path"
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


def device_spec(platform: str, raw: Any, manifest_dir: Path, build: Dict[str, str]) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PackageBuildError("{} must be an object".format(platform))
    allowed = {"sys_env_version", "build_time", "branch_name", "commit_id", "modules", "resource", "resources", "zjhrobot", "scripts"}
    if set(raw) - allowed:
        raise PackageBuildError("{} has unsupported keys".format(platform))
    if "resource" in raw and "resources" in raw:
        raise PackageBuildError("{} may use resource or resources, not both".format(platform))
    for field in ("sys_env_version", "build_time", "branch_name", "commit_id"):
        if field in raw:
            require_string(raw[field], "{}.{}".format(platform, field), allow_empty=True)
    if "zjhrobot" in raw and not isinstance(raw["zjhrobot"], dict):
        raise PackageBuildError("{}.zjhrobot must be an object".format(platform))
    return {"platform": platform, "sys_env_version": raw.get("sys_env_version", ""), "build_time": raw.get("build_time") or build["build_time"], "branch_name": raw.get("branch_name") or build["branch_name"], "commit_id": raw.get("commit_id") or build["commit_id"], "modules": module_specs(platform, raw, manifest_dir), "resources": resource_specs(platform, raw, manifest_dir), "hooks": hook_specs(platform, raw, manifest_dir), "zjhrobot": raw.get("zjhrobot", {})}


def load_manifest(path: Path) -> Dict[str, Any]:
    data = load_jsonc(path)
    allowed = {"schema_version", "version", "build_time", "branch_name", "commit_id", "ORIN", "PICO", "output_name"}
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
        raise PackageBuildError("manifest must contain ORIN and/or PICO")
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


def verify_checksum(path: Path, digest: str, expected: Optional[str]) -> None:
    if expected is not None and digest.lower() != expected.lower():
        path.unlink(missing_ok=True)
        raise PackageBuildError("SHA256 mismatch for {}".format(path.name))


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
    return [
        "# Managed by Middleware run package. Source this file before starting modules.",
        item("MIDDLEWARE_PLATFORM", device["platform"]), item("MIDDLEWARE_VERSION", build["version"]),
        item("MIDDLEWARE_BUILD_TIME", device["build_time"]), item("MIDDLEWARE_BRANCH_NAME", device["branch_name"]),
        item("MIDDLEWARE_COMMIT_ID", device["commit_id"]), item("MIDDLEWARE_SYS_ENV_VERSION", device["sys_env_version"]),
        item("MIDDLEWARE_MODULES", modules), item("MIDDLEWARE_PACKAGE_ID", "{}:{}:{}".format(build["version"], device["platform"], device["commit_id"])),
    ]


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
    lines = ["#!/bin/sh", "set -eu", 'action="${1:-install}"', 'if [ "$#" -gt 0 ]; then shift; fi', 'if [ "$#" -ne 0 ]; then echo "ERROR: unexpected arguments" >&2; exit 2; fi', 'if [ "$(id -u)" -ne 0 ]; then echo "ERROR: run with sudo" >&2; exit 1; fi', 'package_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)', 'case "$action" in install|uninstall) ;; *) echo "ERROR: action must be install or uninstall" >&2; exit 2;; esac', "", 'if [ "$action" = install ]; then']
    lines.extend("    " + line for line in render_hook_lines(device, "pre_install"))
    for module in device["modules"]:
        lines.append('    dpkg -i "$package_root/{}"'.format(module["destination"])) if module["kind"] == "deb" else lines.append("    docker pull {}".format(shlex.quote(module["image"])))
    for resource in device["resources"]:
        lines.append('    install -D -m 0644 "$package_root/{}" {}'.format(resource["destination"], shlex.quote(resource["device_path"])))
    lines.extend(["    env_dir={}".format(shlex.quote(str(PurePosixPath(env_path).parent))), "    mkdir -p \"$env_dir\"", "    env_tmp=$(mktemp \"$env_dir/.Middleware.env.XXXXXX\")", "    {"])
    for line in environment_lines(build, device):
        lines.append("        printf '%s\\n' {}".format(shlex.quote(line)))
    lines.extend(["    } > \"$env_tmp\"", "    chmod 0644 \"$env_tmp\"", "    mv \"$env_tmp\" {}".format(shlex.quote(env_path))])
    lines.extend("    " + line for line in render_hook_lines(device, "post_install"))
    lines.extend(["    exit 0", "fi", ""])
    lines.extend(render_hook_lines(device, "pre_uninstall"))
    for module in reversed(device["modules"]):
        if module["kind"] == "deb":
            lines.append("dpkg -r {}".format(shlex.quote(module["name"])))
    lines.extend(["if [ -f {} ] && grep -Fqx {} {}; then".format(shlex.quote(env_path), shlex.quote(expected), shlex.quote(env_path)), "    rm -f {}".format(shlex.quote(env_path)), "fi"])
    lines.extend(render_hook_lines(device, "post_uninstall"))
    return "\n".join(lines) + "\n"


def render_launcher(devices: Sequence[Dict[str, Any]]) -> str:
    available = {device["platform"] for device in devices}
    lines = ["#!/bin/sh", "set -eu", 'action="install"', 'device=""', 'while [ "$#" -gt 0 ]; do', '    case "$1" in', '        install|uninstall) action="$1" ;;', '        --device) shift; [ "$#" -gt 0 ] || { echo "ERROR: --device needs ORIN or PICO" >&2; exit 2; }; device="$1" ;;', '        --device=*) device="${1#--device=}" ;;', '        -h|--help) echo "Usage: $0 [install|uninstall] [--device ORIN|PICO]"; exit 0 ;;', '        *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;', '    esac', '    shift', 'done', 'if [ -z "$device" ]; then']
    if available == {"ORIN"}:
        lines.append('    device="ORIN"')
    elif available == {"PICO"}:
        lines.append('    device="PICO"')
    else:
        lines.extend(['    if [ -d /etc/naviai ] || id naviai >/dev/null 2>&1; then device="ORIN"; fi', '    if [ -d /etc/nav01 ] || id nav01 >/dev/null 2>&1; then', '        if [ -n "$device" ]; then echo "ERROR: both ORIN and PICO were detected; use --device" >&2; exit 2; fi', '        device="PICO"', '    fi', '    [ -n "$device" ] || { echo "ERROR: cannot detect device; use --device ORIN|PICO" >&2; exit 2; }'])
    lines.append('case "$device" in')
    for platform in PLATFORMS:
        if platform in available:
            lines.append("    {}) exec \"$(dirname \"$0\")/{}/run.sh\" \"$action\" ;;".format(platform, platform))
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


def build(manifest_path: Path, output_dir: Path, dry_run: bool = False) -> Path:
    manifest = load_manifest(manifest_path)
    manifest_dir = manifest_path.parent.resolve()
    output = output_dir.resolve() / output_filename(manifest)
    print("Middleware {} -> {}".format(manifest["build"]["version"], output))
    for device in manifest["devices"]:
        print("{}: {} module(s), {} resource(s)".format(device["platform"], len(device["modules"]), len(device["resources"])))
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
            script_path = stage_root / device["platform"] / "run.sh"
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(render_device_script(manifest["build"], device), encoding="utf-8")
            script_path.chmod(0o755)
        (stage_root / "launcher.sh").write_text(render_launcher(manifest["devices"]), encoding="utf-8")
        (stage_root / "launcher.sh").chmod(0o755)
        (stage_root / "package-manifest.json").write_text(json.dumps({"schema_version": SCHEMA_VERSION, "build": manifest["build"], "devices": manifest["devices"]}, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
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
