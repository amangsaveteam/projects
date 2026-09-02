#!/usr/bin/env python3
"""Build a target-aware one-stop installer from version.json and package URLs."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import time
import tempfile
import urllib.request
from pathlib import Path

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
SHA = re.compile(r"^[0-9a-fA-F]{64}$")


class BuildError(RuntimeError):
    pass


def uncomment(value):
    output, quote, index = [], "", 0
    while index < len(value):
        char = value[index]
        next_char = value[index + 1] if index + 1 < len(value) else ""
        if quote:
            output.append(char)
            if char == "\\" and index + 1 < len(value):
                index += 1
                output.append(value[index])
            elif char == quote:
                quote = ""
        elif char in ("'", '"'):
            quote = char
            output.append(char)
        elif char == "/" and next_char == "/":
            index += 2
            while index < len(value) and value[index] not in "\r\n":
                index += 1
            continue
        else:
            output.append(char)
        index += 1
    return "".join(output)


def load(path):
    try:
        data = json.loads(uncomment(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise BuildError("cannot read {}: {}".format(path, error)) from error
    if not isinstance(data, dict):
        raise BuildError("{} must contain an object".format(path))
    return data


def require(value, name, safe=False):
    if not isinstance(value, str) or not value:
        raise BuildError("{} must be a non-empty string".format(name))
    if safe and not TOKEN.fullmatch(value):
        raise BuildError("{} has unsupported characters".format(name))
    return value


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(url, destination, expected, dry_run, retries=3):
    if dry_run:
        print("Would download {}".format(url))
        return
    print("Download {}".format(url))
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)
            last_error = None
            break
        except OSError as error:
            last_error = error
            if attempt < retries:
                print("  retry {}/{} for {}: {}".format(attempt, retries, url, error))
                time.sleep(2 * attempt)
    if last_error:
        raise BuildError("download failed for {}: {}".format(url, last_error)) from last_error
    actual = file_sha256(destination)
    if expected and actual != expected.lower():
        raise BuildError("SHA256 mismatch for {}".format(url))


def resolve_installers(deb_path, values, field, dry_run):
    installers = [require(value, field) for value in values]
    if installers != ["auto"]:
        return installers
    if dry_run:
        print("Would inspect {} for its installer alias".format(deb_path.name))
        return ["/usr/sbin/<auto-detected>"]
    result = subprocess.run(["dpkg-deb", "-c", str(deb_path)], text=True, capture_output=True, check=True)
    candidates = []
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[0].startswith("-rwx"):
            continue
        path = "/" + fields[-1][2:]
        if path.startswith("/usr/sbin/"):
            candidates.append(path)
        elif path.startswith("/usr/lib/") and re.search(r"install.*deps.*\.sh$", path):
            candidates.append(path)
    if len(candidates) > 1:
        deps_only = [c for c in candidates if c.endswith("/install_deps.sh")]
        if len(deps_only) == 1:
            candidates = deps_only
    if len(candidates) != 1:
        raise BuildError("{}: expected one installer, found {}".format(field, candidates))
    return candidates


def header():
    lines = ["#!/bin/sh", "set -eu", "archive_line=10", "work_dir=$(mktemp -d \"${TMPDIR:-/tmp}/navi-one-stop.XXXXXX\")", "cleanup() { rm -rf \"$work_dir\"; }", "trap cleanup EXIT HUP INT TERM", "tail -n +\"$archive_line\" \"$0\" | tar -xzf - -C \"$work_dir\"", "exec \"$work_dir/install.sh\" \"$@\"", "__ARCHIVE_BELOW__", ""]
    return "\n".join(lines).encode("utf-8")


def target_install(common_rel, common, extras, runs):
    tool = require(common.get("configure_tool"), "common.configure_tool")
    configure_target = require(common.get("configure_target"), "common.configure_target", safe=True)
    installer = require(common.get("installer"), "common.installer")
    lines = ["#!/bin/bash", "set -euo pipefail", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"", "robot_type=\"$1\"", "dpkg -i \"$root/{}\"".format(common_rel), "python3 \"{}\" configure --target \"{}\" --robot-type \"$robot_type\"".format(tool, configure_target), "\"{}\"".format(installer)]
    for relpath, installers in extras:
        lines.append("dpkg -i \"$root/{}\"".format(relpath))
        lines.extend("\"{}\"".format(item) for item in installers)
    lines.extend("/bin/bash \"$root/{}\" -- --robot-type \"$robot_type\"".format(item) for item in runs)
    return "\n".join(lines) + "\n"


def master_install(rows, version):
    table = "\\n".join("|".join(item) for item in rows)
    lines = ["#!/bin/bash", "set -euo pipefail", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"", "target=\"\"", "robot_type=\"\"", "action=install", "while [[ $# -gt 0 ]]; do", "  case \"$1\" in", "    --) ;;", "    --target) shift; target=\"${1:?--target needs a value}\" ;;", "    --target=*) target=\"${1#--target=}\" ;;", "    --robot-type) shift; robot_type=\"${1:?--robot-type needs a value}\" ;;", "    --robot-type=*) robot_type=\"${1#--robot-type=}\" ;;", "    --list-targets|--info|--verify) action=\"$1\" ;;", "    -h|--help) echo \"Usage: $0 [--target TARGET] --robot-type TYPE\"; exit 0 ;;", "    *) echo \"ERROR: unknown argument: $1\" >&2; exit 2 ;;", "  esac", "  shift", "done", "table=$'{}'".format(table), "if [[ \"$action\" == --list-targets ]]; then printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi", "if [[ \"$action\" == --info ]]; then echo \"Version: {}\"; printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi".format(version), "if [[ \"$action\" == --verify ]]; then (cd \"$root\" && sha256sum -c payloads.sha256); exit $?; fi", "[[ $EUID -eq 0 ]] || { echo \"ERROR: run as root\" >&2; exit 1; }", "[[ -n \"$robot_type\" ]] || { echo \"ERROR: --robot-type is required\" >&2; exit 2; }", "if [[ -z \"$target\" ]]; then", "  os_id=\"\"; os_version=\"\"", "  [[ -r /etc/os-release ]] && . /etc/os-release && os_id=\"${ID:-}\" && os_version=\"${VERSION_ID:-}\"", "  case \"$(uname -m)\" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) echo \"ERROR: unsupported architecture\" >&2; exit 2 ;; esac", "  while IFS='|' read -r candidate candidate_os candidate_version candidate_arch; do", "    if [[ \"${os_id,,}\" == \"$candidate_os\" && \"$os_version\" == \"$candidate_version\" && \"$arch\" == \"$candidate_arch\" ]]; then target=\"$candidate\"; break; fi", "  done <<< \"$table\"", "fi", "[[ -n \"$target\" && -x \"$root/targets/$target/install.sh\" ]] || { echo \"ERROR: target unavailable: $target\" >&2; exit 2; }", "exec \"$root/targets/$target/install.sh\" \"$robot_type\"", ""]
    return "\n".join(lines)


def build(version_file, urls_file, output_dir, dry_run=False):
    version_data, urls_data = load(version_file), load(urls_file)
    if version_data.get("schema_version") != 1 or urls_data.get("schema_version") != 1:
        raise BuildError("schema_version must be 1")
    version = require(version_data.get("version"), "version", safe=True)
    output_name = require(version_data.get("output_name"), "output_name", safe=True)
    raw_targets = urls_data.get("targets")
    if not isinstance(raw_targets, dict):
        raise BuildError("targets must be an object")
    with tempfile.TemporaryDirectory(prefix="navi-one-stop-") as temporary:
        stage, rows, checksums = Path(temporary) / "stage", [], []
        for target_id, target in raw_targets.items():
            target_id = require(target_id, "target id", safe=True)
            common = target.get("common") if isinstance(target, dict) else None
            if not isinstance(common, dict) or not common.get("url"):
                continue
            os_id = require(target.get("os_id"), target_id + ".os_id").lower()
            os_version = require(target.get("os_version"), target_id + ".os_version")
            arch = require(target.get("architecture"), target_id + ".architecture", safe=True)
            common_rel = "payloads/{}/common.deb".format(target_id)
            common_path = stage / common_rel
            common_path.parent.mkdir(parents=True, exist_ok=True)
            expected = str(common.get("sha256", ""))
            if expected and not SHA.fullmatch(expected): raise BuildError(target_id + ".common.sha256 is invalid")
            download(require(common["url"], target_id + ".common.url"), common_path, expected, dry_run)
            if not dry_run: checksums.append((file_sha256(common_path), common_rel))
            extras, runs = [], []
            for index, item in enumerate(target.get("extra_debs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/extra-{:02d}.deb".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".extra.url"), path, expected, dry_run)
                if not dry_run: checksums.append((file_sha256(path), relpath))
                extras.append((relpath, resolve_installers(path, item.get("installers", []), target_id + ".extra.installers", dry_run)))
            for index, item in enumerate(target.get("runs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/run-{:02d}.run".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".run.url"), path, expected, dry_run)
                if not dry_run: path.chmod(0o755); checksums.append((file_sha256(path), relpath))
                runs.append(relpath)
            script = stage / "targets" / target_id / "install.sh"; script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(target_install(common_rel, common, extras, runs), encoding="utf-8"); script.chmod(0o755)
            rows.append((target_id, os_id, os_version, arch))
        if not rows: raise BuildError("no target has common.url configured")
        output = output_dir / (output_name + ".run")
        if dry_run:
            print("Configured targets: " + ", ".join(item[0] for item in rows)); return output
        (stage / "payloads.sha256").write_text("".join("{}  {}\n".format(value, path) for value, path in sorted(checksums)), encoding="utf-8")
        install = stage / "install.sh"; install.write_text(master_install(rows, version), encoding="utf-8"); install.chmod(0o755)
        output_dir.mkdir(parents=True, exist_ok=True); temporary_output = output.with_name("." + output.name + ".tmp")
        with temporary_output.open("wb") as stream:
            stream.write(header())
            with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for item in sorted(stage.rglob("*")):
                    if item.is_file(): archive.add(item, arcname=item.relative_to(stage).as_posix(), recursive=False)
        temporary_output.chmod(0o755); temporary_output.replace(output)
    print("Built {}".format(output)); return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, type=Path); parser.add_argument("--urls", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/one-stop")); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try: build(args.version.resolve(), args.urls.resolve(), args.output_dir.resolve(), args.dry_run)
    except BuildError as error: print("ERROR: {}".format(error), file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
