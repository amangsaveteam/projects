#!/usr/bin/env python3
"""Build a target-aware one-stop installer from version.json and package URLs."""
import argparse
import hashlib
import json
import re
import shlex
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
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$")
ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEPLOYMENT_ROOT = Path(__file__).resolve().parents[1]
COMMON_ROOT = DEPLOYMENT_ROOT / "common"
MIDDLEWARE_TEMPLATES = {
    "ORIN": "Middleware.orin.env",
    "PICO": "Middleware.pico.env",
    "RDK": "Middleware.rdk.env",
}


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


def resolve_environment(values, field):
    if values is None:
        return []
    if not isinstance(values, dict):
        raise BuildError(field + " must be an object")
    result = []
    for name, value in sorted(values.items()):
        if not isinstance(name, str) or not ENVIRONMENT_KEY.fullmatch(name):
            raise BuildError(field + " contains an invalid variable name")
        if not isinstance(value, str) or "\x00" in value:
            raise BuildError(field + ".{} must be a string without NUL".format(name))
        result.append("{}={}".format(name, shlex.quote(value)))
    return result


def services_from_run(run_path):
    """Return service units embedded by a Middleware-format run package."""
    try:
        with run_path.open("rb") as stream:
            archive_line = None
            for _ in range(64):
                match = re.fullmatch(rb"archive_line=(\d+)\n?", stream.readline())
                if match:
                    archive_line = int(match.group(1))
                    break
            if archive_line is None:
                return []
            stream.seek(0)
            for _ in range(archive_line - 1):
                stream.readline()
            with tarfile.open(fileobj=stream, mode="r:gz") as archive:
                result = []
                for member in archive.getmembers():
                    match = re.fullmatch(r"(?:ORIN|PICO|RDK)/startup/([A-Za-z0-9][A-Za-z0-9_.@-]*\.service)", member.name)
                    if match:
                        result.append(match.group(1))
                return sorted(set(result))
    except (OSError, tarfile.TarError):
        return []


def header():
    lines = ["#!/bin/sh", "set -eu", "archive_line=10", "work_dir=$(mktemp -d \"${TMPDIR:-/tmp}/navi-one-stop.XXXXXX\")", "cleanup() { rm -rf \"$work_dir\"; }", "trap cleanup EXIT HUP INT TERM", "tail -n +\"$archive_line\" \"$0\" | tar -xzf - -C \"$work_dir\"", "exec \"$work_dir/install.sh\" \"$@\"", "__ARCHIVE_BELOW__", ""]
    return "\n".join(lines).encode("utf-8")


def system_config_installer(target_id, configure_target, config_rel):
    middleware = require(MIDDLEWARE_TEMPLATES.get(target_id.split("-", 1)[0].upper(), ""), "system config template")
    environment_dir = "/etc/nav01" if target_id.startswith("pico-") else "/etc/naviai"
    lines = [
        "#!/bin/bash", "set -euo pipefail",
        "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"", "robot_type=\"${1:?robot type is required}\"",
        "config_root=\"$root/{}\"".format(config_rel),
        "install -d -m 0755 /etc/zj_humanoid {}".format(environment_dir),
        "install -m 0644 \"$config_root/zj_humanoid.sh\" /etc/profile.d/zj_humanoid.sh",
        "install -m 0644 \"$config_root/cyclonedds.xml\" /etc/zj_humanoid/cyclonedds.xml",
        "install -m 0644 \"$config_root/{}\" {}/Middleware.env".format(middleware, environment_dir),
        "python3 \"$config_root/deploy_common.py\" configure --target \"{}\" --robot-type \"$robot_type\"".format(configure_target),
        "bashrc=/etc/bash.bashrc", "begin='# BEGIN zj-humanoid common environment'",
        "if ! grep -Fqx \"$begin\" \"$bashrc\" 2>/dev/null; then",
        "  cat >> \"$bashrc\" <<'EOF'", "", "# BEGIN zj-humanoid common environment",
        "if [ -r /etc/profile.d/zj_humanoid.sh ]; then", "    . /etc/profile.d/zj_humanoid.sh", "fi",
        "# END zj-humanoid common environment", "EOF", "fi",
    ]
    return "\n".join(lines) + "\n"


def stage_system_config(stage, target_id, target, checksums, dry_run):
    system_config = target.get("system_config") if isinstance(target, dict) else None
    if not isinstance(system_config, dict):
        return None
    configure_target = require(system_config.get("configure_target"), target_id + ".system_config.configure_target", safe=True)
    targets_path = COMMON_ROOT / "configs" / "targets.json"
    target_data = load(targets_path).get("targets", {}).get(configure_target)
    if not isinstance(target_data, dict):
        raise BuildError("unknown system configuration target: {}".format(configure_target))
    device = require(str(target_data.get("device", "")), configure_target + ".device", safe=True)
    template = MIDDLEWARE_TEMPLATES.get(device)
    if template is None:
        raise BuildError("unsupported system configuration device: {}".format(device))
    config_rel = "payloads/{}/system-config".format(target_id)
    config_path = stage / config_rel
    if not dry_run:
        config_path.mkdir(parents=True, exist_ok=True)
        assets = {
            "zj_humanoid.sh": COMMON_ROOT / "files/etc/profile.d/zj_humanoid.sh",
            "cyclonedds.xml": COMMON_ROOT / "files/etc/zj_humanoid/cyclonedds.xml",
            "deploy_common.py": COMMON_ROOT / "deploy_common.py",
            "configs/targets.json": COMMON_ROOT / "configs/targets.json",
            "configs/robot-types.json": COMMON_ROOT / "configs/robot-types.json",
            template: COMMON_ROOT / "templates" / template,
        }
        for name, source in assets.items():
            if not source.is_file():
                raise BuildError("system configuration source is missing: {}".format(source))
            destination = config_path / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if name == "deploy_common.py": destination.chmod(0o755)
            checksums.append((file_sha256(destination), (Path(config_rel) / name).as_posix()))
        installer = stage / "targets" / target_id / "install-system-config.sh"
        installer.parent.mkdir(parents=True, exist_ok=True)
        installer.write_text(system_config_installer(target_id, configure_target, config_rel), encoding="utf-8")
        installer.chmod(0o755)
    return config_rel


def target_install(target_id, system_config_rel, common_rel, common, extras, runs, services):
    lines = ["#!/bin/bash", "set -euo pipefail", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"", "robot_type=\"$1\"", "(cd \"$root\" && sha256sum -c \"targets/{}/payloads.sha256\")".format(target_id)]
    if services:
        lines.extend([
            "managed_services=(" + " ".join('\"{}\"'.format(item) for item in services) + ")",
            "stop_managed_services() {", "  local unit", "  for unit in \"${managed_services[@]}\"; do",
            "    if systemctl is-active --quiet \"$unit\"; then", "      echo \"Stopping $unit\"", "      systemctl stop \"$unit\"", "    fi",
            "  done", "}", "install_complete=0", "on_install_exit() {", "  status=$?",
            "  if [[ \"$install_complete\" -ne 1 ]]; then", "    echo \"ERROR: overall installation failed; managed services are being kept stopped.\" >&2",
            "    stop_managed_services || true", "  fi", "  exit \"$status\"", "}", "trap on_install_exit EXIT", "stop_managed_services",
        ])
    if system_config_rel:
        lines.append("/bin/bash \"$root/targets/{}/install-system-config.sh\" \"$robot_type\"".format(target_id))
    elif isinstance(common, dict):
        tool = require(common.get("configure_tool"), "common.configure_tool")
        configure_target = require(common.get("configure_target"), "common.configure_target", safe=True)
        installer = require(common.get("installer"), "common.installer")
        lines.extend(["dpkg -i \"$root/{}\"".format(common_rel), "python3 \"{}\" configure --target \"{}\" --robot-type \"$robot_type\"".format(tool, configure_target), "\"{}\"".format(installer)])
    else:
        raise BuildError(target_id + " must configure system_config or common")
    for relpath, installers, environment in extras:
        lines.append("dpkg -i \"$root/{}\"".format(relpath))
        prefix = "env " + " ".join(environment) + " " if environment else ""
        lines.extend(prefix + "\"{}\"".format(item) for item in installers)
    for item in runs:
        lines.append("/bin/bash \"$root/{}\" -- --robot-type \"$robot_type\"".format(item))
        if services:
            lines.append("stop_managed_services")
    if services:
        lines.extend([
            "systemctl daemon-reload", "for unit in \"${managed_services[@]}\"; do",
            "  if [[ -f \"/etc/systemd/system/$unit\" ]]; then", "    systemctl enable \"$unit\"", "    systemctl restart \"$unit\"", "  fi",
            "done", "install_complete=1", "trap - EXIT",
        ])
    return "\n".join(lines) + "\n"


def target_pretest(target_id, system_config_rel, common_rel, extras, runs):
    lines = [
        "#!/bin/bash", "set -euo pipefail", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"",
        "echo \"Target: {}\"".format(target_id),
        "pretest_deb() {", "  local payload=$1 package expected installed installed_version installed_status action",
        "  package=$(dpkg-deb -f \"$payload\" Package)", "  expected=$(dpkg-deb -f \"$payload\" Version)",
        "  installed=$(dpkg-query -W -f='${Version}\\t${db:Status-Status}' \"$package\" 2>/dev/null || true)",
        "  installed_version=${installed%%$'\\t'*}", "  installed_status=${installed#*$'\\t'}",
        "  if [[ -z \"$installed\" || \"$installed_status\" != installed ]]; then action=install; installed_version='not installed'",
        "  elif [[ \"$installed_version\" == \"$expected\" ]]; then action=reinstall",
        "  elif dpkg --compare-versions \"$installed_version\" gt \"$expected\"; then action='downgrade blocked'",
        "  else action=upgrade; fi",
        "  printf '%s=%s | %s | %s\\n' \"$package\" \"$expected\" \"$installed_version\" \"$action\"", "}",
        "pretest_run() {", "  local payload=$1 run_help",
        "  run_help=$(/bin/bash \"$payload\" -- --help 2>&1 || true)",
        "  if grep -Fq -- '--pretest' <<<\"$run_help\"; then",
        "    /bin/bash \"$payload\" -- --pretest",
        "  elif grep -Fq -- '--packages' <<<\"$run_help\"; then",
        "    echo 'WARN: embedded run has no --pretest; listing its declared packages only.' >&2",
        "    /bin/bash \"$payload\" -- --packages || echo 'WARN: embedded package manifest could not be read.' >&2",
        "  else", "    echo 'WARN: embedded run exposes no readable package manifest.' >&2", "  fi", "}",
    ]
    if system_config_rel:
        lines.append("echo 'System configuration: deploy/update'")
    elif common_rel:
        lines.append("pretest_deb \"$root/{}\"".format(common_rel))
    for relpath, _, _ in extras:
        lines.append("pretest_deb \"$root/{}\"".format(relpath))
    for relpath in runs:
        lines.extend([
            "echo \"Run package: {}\"".format(relpath),
            "pretest_run \"$root/{}\"".format(relpath),
        ])
    return "\n".join(lines) + "\n"


def master_install(rows, version):
    table = "\\n".join("|".join(item) for item in rows)
    lines = [
        "#!/bin/bash", "set -euo pipefail", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"",
        "target=\"\"", "robot_type=\"\"", "robot_type_source=\"\"", "robot_type_error=\"\"", "action=install",
        "while [[ $# -gt 0 ]]; do", "  case \"$1\" in", "    --) ;;",
        "    --target) shift; target=\"${1:?--target needs a value}\" ;;", "    --target=*) target=\"${1#--target=}\" ;;",
        "    --robot-type) shift; robot_type=\"${1:?--robot-type needs a value}\"; robot_type_source=argument ;;", "    --robot-type=*) robot_type=\"${1#--robot-type=}\"; robot_type_source=argument ;;",
        "    --list-targets|--info|--verify|--pretest) action=\"$1\" ;;", "    -h|--help) echo \"Usage: $0 [--target TARGET] [--robot-type TYPE] [--pretest]\"; exit 0 ;;",
        "    *) echo \"ERROR: unknown argument: $1\" >&2; exit 2 ;;", "  esac", "  shift", "done",
        "resolve_robot_type() {", "  local configured=\"\"", "  if [[ -n \"$robot_type\" ]]; then", "    [[ \"$robot_type\" =~ ^[A-Za-z0-9_-]+$ ]] || { robot_type_error=\"invalid --robot-type: $robot_type\"; return; }", "    return", "  fi",
        "  if [[ -r /etc/zj_humanoid/device.env ]]; then", "    configured=$(sed -n 's/^ROBOT_TYPE=//p' /etc/zj_humanoid/device.env | head -n 1)",
        "    if [[ -n \"$configured\" ]]; then", "      if [[ \"$configured\" =~ ^[A-Za-z0-9_-]+$ ]]; then robot_type=$configured; robot_type_source=device.env; else robot_type_error=\"invalid ROBOT_TYPE in /etc/zj_humanoid/device.env\"; fi", "    fi", "  fi", "}",
        "table=$'{}'".format(table), "if [[ \"$action\" == --list-targets ]]; then printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi",
        "if [[ \"$action\" == --info ]]; then echo \"Version: {}\"; printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi".format(version),
        "if [[ \"$action\" == --verify ]]; then (cd \"$root\" && sha256sum -c payloads.sha256); exit $?; fi",
        "if [[ -z \"$target\" ]]; then", "  os_id=\"\"; os_version=\"\"", "  [[ -r /etc/os-release ]] && . /etc/os-release && os_id=\"${ID:-}\" && os_version=\"${VERSION_ID:-}\"",
        "  case \"$(uname -m)\" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) echo \"ERROR: unsupported architecture\" >&2; exit 2 ;; esac",
        "  while IFS='|' read -r candidate candidate_os candidate_version candidate_arch; do", "    if [[ \"${os_id,,}\" == \"$candidate_os\" && \"$os_version\" == \"$candidate_version\" && \"$arch\" == \"$candidate_arch\" ]]; then target=\"$candidate\"; break; fi", "  done <<< \"$table\"", "fi",
        "[[ -n \"$target\" && -x \"$root/targets/$target/install.sh\" ]] || { echo \"ERROR: target unavailable: $target\" >&2; exit 2; }",
        "resolve_robot_type", "if [[ \"$action\" == --pretest ]]; then", "  if [[ -n \"$robot_type\" ]]; then echo \"Robot type: $robot_type ($robot_type_source)\"; else echo \"Robot type: not configured; bare device installation requires --robot-type TYPE\"; fi", "  [[ -z \"$robot_type_error\" ]] || echo \"WARN: $robot_type_error\" >&2", "  exec \"$root/targets/$target/pretest.sh\"", "fi",
        "[[ $EUID -eq 0 ]] || { echo \"ERROR: run as root\" >&2; exit 1; }", "[[ -z \"$robot_type_error\" ]] || { echo \"ERROR: $robot_type_error\" >&2; exit 2; }", "[[ -n \"$robot_type\" ]] || { echo \"ERROR: bare device requires --robot-type TYPE\" >&2; exit 2; }",
        "exec \"$root/targets/$target/install.sh\" \"$robot_type\"", "",
    ]
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
            system_config = target.get("system_config") if isinstance(target, dict) else None
            if not isinstance(system_config, dict) and (not isinstance(common, dict) or not common.get("url")):
                continue
            os_id = require(target.get("os_id"), target_id + ".os_id").lower()
            os_version = require(target.get("os_version"), target_id + ".os_version")
            arch = require(target.get("architecture"), target_id + ".architecture", safe=True)
            target_checksums = []
            system_config_rel = stage_system_config(stage, target_id, target, target_checksums, dry_run)
            common_rel = ""
            if not system_config_rel:
                common_rel = "payloads/{}/common.deb".format(target_id)
                common_path = stage / common_rel
                common_path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(common.get("sha256", ""))
                if expected and not SHA.fullmatch(expected): raise BuildError(target_id + ".common.sha256 is invalid")
                download(require(common["url"], target_id + ".common.url"), common_path, expected, dry_run)
                if not dry_run: target_checksums.append((file_sha256(common_path), common_rel))
            extras, runs = [], []
            configured_services = target.get("managed_services", [])
            if not isinstance(configured_services, list) or any(not isinstance(item, str) or not SERVICE.fullmatch(item) for item in configured_services):
                raise BuildError(target_id + ".managed_services must be a list of systemd unit names")
            services = list(configured_services)
            for index, item in enumerate(target.get("extra_debs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/extra-{:02d}.deb".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".extra.url"), path, expected, dry_run)
                if not dry_run: target_checksums.append((file_sha256(path), relpath))
                extras.append((
                    relpath,
                    resolve_installers(path, item.get("installers", []), target_id + ".extra.installers", dry_run),
                    resolve_environment(item.get("environment"), target_id + ".extra.environment"),
                ))
            for index, item in enumerate(target.get("runs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/run-{:02d}.run".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".run.url"), path, expected, dry_run)
                if not dry_run:
                    path.chmod(0o755)
                    target_checksums.append((file_sha256(path), relpath))
                    services.extend(services_from_run(path))
                runs.append(relpath)
            script = stage / "targets" / target_id / "install.sh"; script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(target_install(target_id, system_config_rel, common_rel, common, extras, runs, sorted(set(services))), encoding="utf-8"); script.chmod(0o755)
            pretest = stage / "targets" / target_id / "pretest.sh"
            pretest.write_text(target_pretest(target_id, system_config_rel, common_rel, extras, runs), encoding="utf-8")
            pretest.chmod(0o755)
            if not dry_run:
                target_manifest = stage / "targets" / target_id / "payloads.sha256"
                target_manifest.write_text("".join("{}  {}\n".format(value, path) for value, path in sorted(target_checksums)), encoding="utf-8")
                checksums.extend(target_checksums)
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
