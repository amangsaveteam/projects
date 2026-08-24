#!/usr/bin/env python3
"""Build a multi-payload offline common Debian carrier from a manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


COMMON_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = COMMON_DIR.parents[2]


def parse_manifest(manifest_path: Path) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            raise ValueError(f"{manifest_path}:{line_number}: expected three TAB-separated fields")
        packages.append((fields[0], fields[1]))
    if not packages:
        raise ValueError(f"{manifest_path}: contains no packages")
    return packages


def deb_field(deb_path: Path, field: str) -> str:
    result = subprocess.run(
        ["dpkg-deb", "-f", str(deb_path), field],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def version_at_least(version: str, minimum_version: str) -> bool:
    return minimum_version == "0" or subprocess.run(
        ["dpkg", "--compare-versions", version, "ge", minimum_version], check=False
    ).returncode == 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configured_path(value: str, *, description: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe {description}: {value}")
    return path


def copy_extra_files(staging: Path, config: dict[str, object]) -> None:
    conffiles: list[str] = []
    for extra_file in config.get("extra_files", []):
        if not isinstance(extra_file, dict):
            raise ValueError("extra_files entries must be objects")
        source = PROJECT_ROOT / str(extra_file["source"])
        destination = configured_path(str(extra_file["destination"]), description="extra file destination")
        if not source.is_file():
            raise FileNotFoundError(f"configured extra file is missing: {source}")
        target = staging / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        os.chmod(target, int(str(extra_file.get("mode", "0644")), 8))
        if bool(extra_file.get("conffile", False)):
            conffiles.append(f"/{destination.as_posix()}")

    if conffiles:
        (staging / "DEBIAN/conffiles").write_text("".join(f"{entry}\n" for entry in conffiles), encoding="utf-8")


def write_installer(staging: Path, package_name: str, aliases: list[str], device_config_target: str) -> None:
    payload_directory = f"/usr/lib/{package_name}/payload"
    config_tool = f"/usr/lib/{package_name}/deploy_common.py"
    script = f'''#!/bin/bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this installer as root, for example: sudo $0" >&2
    exit 1
fi

if ! python3 {config_tool} validate-config --target {device_config_target}; then
    echo "ROBOT_TYPE must be configured before installing common payloads." >&2
    echo "Run: sudo python3 {config_tool} configure --target {device_config_target} --robot-type <model>" >&2
    exit 2
fi

shopt -s nullglob
payloads=({payload_directory}/*.deb)
if (( ${{#payloads[@]}} == 0 )); then
    echo "No offline payloads found in {payload_directory}" >&2
    exit 1
fi

if [[ -f {payload_directory}/payloads.sha256 ]]; then
    (cd {payload_directory} && sha256sum -c payloads.sha256)
fi

dpkg -i "${{payloads[@]}}"
'''
    for alias in aliases:
        alias_path = configured_path(alias, description="installer alias").name
        target = staging / "usr/sbin" / alias_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        os.chmod(target, 0o755)


def write_bash_startup_hook(staging: Path) -> None:
    """Load the shared profile in every new interactive system Bash.

    profile.d is loaded by login shells only. Ubuntu's /etc/bash.bashrc is the
    system-wide hook for non-login interactive Bash sessions, so keep a small
    managed source block there instead of editing any user's ~/.bashrc.
    """
    postinst = '''#!/bin/sh
set -eu

bashrc=/etc/bash.bashrc
begin='# BEGIN zj-humanoid common environment'
end='# END zj-humanoid common environment'

[ -e "$bashrc" ] || : > "$bashrc"
if ! grep -Fqx "$begin" "$bashrc"; then
    cat >> "$bashrc" <<'EOF'

# BEGIN zj-humanoid common environment
if [ -r /etc/profile.d/zj_humanoid.sh ]; then
    . /etc/profile.d/zj_humanoid.sh
fi
# END zj-humanoid common environment
EOF
fi
'''
    postrm = '''#!/bin/sh
set -eu

if [ "${1:-}" = purge ] && [ -f /etc/bash.bashrc ]; then
    temporary=$(mktemp /etc/bash.bashrc.zj-humanoid.XXXXXX)
    sed '/^# BEGIN zj-humanoid common environment$/,/^# END zj-humanoid common environment$/d' \\
        /etc/bash.bashrc > "$temporary"
    cat "$temporary" > /etc/bash.bashrc
    rm -f "$temporary"
fi
'''
    postinst_path = staging / "DEBIAN/postinst"
    postinst_path.write_text(postinst, encoding="utf-8")
    os.chmod(postinst_path, 0o755)
    postrm_path = staging / "DEBIAN/postrm"
    postrm_path.write_text(postrm, encoding="utf-8")
    os.chmod(postrm_path, 0o755)


def download_payloads(packages: list[tuple[str, str]], architecture: str, download_directory: Path) -> list[Path]:
    payloads: list[Path] = []
    for package, minimum_version in packages:
        subprocess.run(["apt-get", "download", package], cwd=download_directory, check=True)
        matching = [deb for deb in download_directory.glob("*.deb") if deb_field(deb, "Package") == package]
        if len(matching) != 1:
            raise RuntimeError(f"expected one downloaded {package} archive, found: {matching}")
        payload = matching[0]
        payload_architecture = deb_field(payload, "Architecture")
        version = deb_field(payload, "Version")
        if payload_architecture not in {architecture, "all"}:
            raise RuntimeError(f"downloaded {package} has architecture {payload_architecture}; expected {architecture} or all")
        if not version_at_least(version, minimum_version):
            raise RuntimeError(f"downloaded {package} version {version} is below {minimum_version}")
        payloads.append(payload)
        print(f"Payload: {package} {version} ({payload_architecture})")
    return payloads


def build(config_path: Path) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    package_name = str(config["package_name"])
    target = config["target"]
    if not isinstance(target, dict):
        raise ValueError("target must be an object")
    manifest_path = PROJECT_ROOT / str(config["manifest"])
    packages = parse_manifest(manifest_path)
    output_dir = PROJECT_ROOT / str(config["output_dir"])
    artifact = output_dir / str(config["artifact_filename"])

    with tempfile.TemporaryDirectory(prefix="navi-common-bundle-") as temporary_directory:
        temporary_path = Path(temporary_directory)
        payloads = download_payloads(packages, str(target["architecture"]), temporary_path)
        staging = temporary_path / "staging"
        (staging / "DEBIAN").mkdir(parents=True)
        payload_directory = staging / "usr/lib" / package_name / "payload"
        payload_directory.mkdir(parents=True)
        for payload in payloads:
            shutil.copy2(payload, payload_directory / payload.name)

        lock_payloads = [
            {
                "name": deb_field(payload, "Package"),
                "version": deb_field(payload, "Version"),
                "architecture": deb_field(payload, "Architecture"),
                "filename": payload.name,
                "sha256": sha256(payload),
            }
            for payload in payloads
        ]
        (payload_directory / "payloads.sha256").write_text(
            "".join(f"{item['sha256']}  {item['filename']}\n" for item in lock_payloads),
            encoding="utf-8",
        )
        lock_path = staging / "usr/lib" / package_name / "manifest.lock.json"
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target": config["id"],
                    "artifact_filename": config["artifact_filename"],
                    "payloads": lock_payloads,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        dependencies = ", ".join(str(item) for item in config.get("deb_depends", []))
        control = (
            f"Package: {package_name}\n"
            f"Version: {config['release_version']}\n"
            "Section: misc\nPriority: optional\n"
            f"Architecture: {target['architecture']}\n"
            "Maintainer: Navi <navi@localhost>\n"
            f"Depends: {dependencies}\n"
            f"Description: {config['description']}\n"
            " Offline payload carrier. Install the carrier, then run its installer alias.\n"
        )
        (staging / "DEBIAN/control").write_text(control, encoding="utf-8")
        aliases = [str(alias) for alias in config.get("installer_aliases", [])]
        if not aliases:
            raise ValueError("at least one installer_alias is required")
        device_config_target = str(config["device_config_target"])
        write_installer(staging, package_name, aliases, device_config_target)
        write_bash_startup_hook(staging)
        copy_extra_files(staging, config)

        output_dir.mkdir(parents=True, exist_ok=True)
        temporary_artifact = output_dir / f".{artifact.name}.tmp"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(staging), str(temporary_artifact)], check=True)
        temporary_artifact.replace(artifact)

    print(f"Built {artifact}")
    print(f"Offline payload count: {len(packages)}")
    print(f"Install carrier with: sudo dpkg -i {artifact.name}")
    print(f"Install payloads with: sudo {aliases[0]}")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path, help="common config JSON")
    arguments = parser.parse_args()
    try:
        build(arguments.config.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
