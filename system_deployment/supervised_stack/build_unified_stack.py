#!/usr/bin/env python3
"""Build one self-extracting installer that dispatches to Pico or Orin stacks."""

import argparse
import json
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping

from build_supervised_stack import (DEPLOYMENT_ROOT, OUTPUT_RE, StackError,
                                    build, checksums, header, write)


TARGETS = {"PICO", "ORIN"}


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StackError("cannot read unified stack manifest {}: {}".format(path, error)) from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise StackError("unified stack manifest schema_version must be 1")
    if set(value) != {"schema_version", "name", "output_name", "targets"}:
        raise StackError("unified stack manifest has unsupported keys")
    if not isinstance(value["name"], str) or not value["name"]:
        raise StackError("unified manifest.name must be a non-empty string")
    if not isinstance(value["output_name"], str) or not OUTPUT_RE.fullmatch(value["output_name"]):
        raise StackError("unified manifest.output_name must contain only safe filename characters")
    targets = value["targets"]
    if not isinstance(targets, dict) or set(targets) != TARGETS:
        raise StackError("unified manifest.targets must define exactly PICO and ORIN")
    for target, relative in targets.items():
        if not isinstance(relative, str) or not relative:
            raise StackError("unified manifest.targets.{} must be a manifest path".format(target))
        source = (DEPLOYMENT_ROOT / relative).resolve()
        if DEPLOYMENT_ROOT not in source.parents or not source.is_file():
            raise StackError("unified target manifest is missing or outside deployment root: {}".format(relative))
    return value


def render_install(manifest: Mapping[str, Any]) -> str:
    name = manifest["name"]
    return """#!/bin/bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
action=install
device=""
robot_type="${ROBOT_TYPE:-}"

usage() {
  cat <<'EOF'
Usage: unified installer -- [--device PICO|ORIN] [--robot-type TYPE] [--pretest|--verify|--info|--uninstall]

Without --device, the installer reads ZJ_DEVICE from /etc/zj_humanoid/device.env
and falls back to the current architecture and Ubuntu version.  --robot-type
defaults to ROBOT_TYPE from the invoking environment or that same device file.
EOF
}

read_device_value() {
  local wanted="$1" key value
  [[ -r /etc/zj_humanoid/device.env ]] || return 0
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    [[ "$key" == "$wanted" ]] || continue
    printf '%s' "$value"
    return 0
  done < /etc/zj_humanoid/device.env
}

detect_device() {
  local configured version arch
  [[ -n "$device" ]] && return 0
  configured="$(read_device_value ZJ_DEVICE)"
  case "$configured" in
    PICO|ORIN) device="$configured"; return 0 ;;
  esac
  version=""
  [[ -r /etc/os-release ]] && . /etc/os-release && version="${VERSION_ID:-}"
  arch="$(uname -m)"
  case "$arch:$version" in
    x86_64:20.04|amd64:20.04) device=PICO ;;
    aarch64:22.04|arm64:22.04) device=ORIN ;;
    *) echo "ERROR: cannot identify Pico or Orin; use --device PICO|ORIN" >&2; exit 2 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --) ;;
    --device) shift; device="${1:?--device needs a value}" ;;
    --device=*) device="${1#--device=}" ;;
    --robot-type) shift; robot_type="${1:?--robot-type needs a value}" ;;
    --robot-type=*) robot_type="${1#--robot-type=}" ;;
    --pretest|--verify|--info|--uninstall) action="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

case "$device" in ""|PICO|ORIN) ;; *) echo "ERROR: --device must be PICO or ORIN" >&2; exit 2 ;; esac
if [[ -z "$robot_type" ]]; then robot_type="$(read_device_value ROBOT_TYPE)"; fi

if [[ "$action" == --info ]]; then
  echo "__NAME__"
  echo "Targets: PICO (Ubuntu 20.04 amd64), ORIN (Ubuntu 22.04 arm64)"
  exit 0
fi
if [[ "$action" == --verify ]]; then
  (cd "$root" && sha256sum -c payloads.sha256)
  exit 0
fi

detect_device
case "$device" in
  PICO) installer="$root/installers/pico.run" ;;
  ORIN) installer="$root/installers/orin.run" ;;
esac

[[ -x "$installer" ]] || { echo "ERROR: missing embedded $device installer" >&2; exit 1; }
if [[ "$action" == --uninstall ]]; then
  exec "$installer" -- --uninstall
fi
[[ -n "$robot_type" ]] || { echo "ERROR: ROBOT_TYPE is unset; configure /etc/zj_humanoid/device.env or pass --robot-type" >&2; exit 2; }
case "$robot_type" in *[!A-Za-z0-9_-]*|'') echo "ERROR: invalid robot type" >&2; exit 2 ;; esac
if [[ "$action" == --pretest ]]; then
  exec "$installer" -- --robot-type "$robot_type" --pretest
fi
exec "$installer" -- --robot-type "$robot_type"
""".replace("__NAME__", name)


def build_unified(manifest_path: Path, output_dir: Path) -> Path:
    manifest = load_manifest(manifest_path)
    with tempfile.TemporaryDirectory(prefix="navi-unified-stack-build-") as temporary:
        temporary_path = Path(temporary)
        stage = temporary_path / "stage"
        child_output = temporary_path / "children"
        stage.mkdir()
        for target in sorted(TARGETS):
            child_manifest = (DEPLOYMENT_ROOT / manifest["targets"][target]).resolve()
            child = build(child_manifest, child_output)
            target_path = stage / "installers" / (target.lower() + ".run")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target_path)
            target_path.chmod(0o755)
        write(stage, "install.sh", render_install(manifest), True)
        write(stage, "package-manifest.json", json.dumps(manifest, indent=2) + "\n")
        checksums(stage)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / (manifest["output_name"] + ".run")
        temporary_output = output.with_name("." + output.name + ".tmp")
        with temporary_output.open("wb") as stream:
            stream.write(header())
            with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
                for item in sorted(stage.rglob("*")):
                    if item.is_file():
                        archive.add(item, arcname=item.relative_to(stage).as_posix(), recursive=False)
        temporary_output.chmod(0o755)
        temporary_output.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEPLOYMENT_ROOT.parent / "dist")
    arguments = parser.parse_args()
    try:
        output = build_unified(arguments.manifest.resolve(), arguments.output_dir.resolve())
    except StackError as error:
        parser.error(str(error))
    print("Built {}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
