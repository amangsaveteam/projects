#!/usr/bin/env python3
"""Build a device-specific, Supervisor-managed module stack from JSON."""

import argparse
import hashlib
import json
import re
import shlex
import shutil
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


DEPLOYMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = DEPLOYMENT_ROOT.parent
ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
OUTPUT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class StackError(RuntimeError):
    pass


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StackError("cannot read stack manifest {}: {}".format(path, error)) from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise StackError("stack manifest schema_version must be 1")
    allowed = {"schema_version", "name", "output_name", "target", "modules"}
    unknown = set(value) - allowed
    if unknown:
        raise StackError("unsupported stack manifest keys: {}".format(", ".join(sorted(unknown))))
    if not isinstance(value.get("name"), str) or not value["name"]:
        raise StackError("manifest.name must be a non-empty string")
    if not isinstance(value.get("output_name"), str) or not OUTPUT_RE.fullmatch(value["output_name"]):
        raise StackError("manifest.output_name must contain only safe filename characters")
    target = value.get("target")
    if not isinstance(target, dict):
        raise StackError("manifest.target must be an object")
    allowed_target = {
        "platform", "os_version", "architecture", "internal_ip", "middleware_env",
        "agent_service", "agent_port", "module_root", "runtime_root", "log_root",
        "agent_modules_directory", "agent_password_file", "environment_files",
    }
    unknown_target = set(target) - allowed_target
    if unknown_target:
        raise StackError("unsupported target keys: {}".format(", ".join(sorted(unknown_target))))
    for key in ("platform", "os_version", "architecture", "internal_ip", "middleware_env", "agent_service"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise StackError("target.{} must be a non-empty string".format(key))
    if not HOST_RE.fullmatch(target["internal_ip"]):
        raise StackError("target.internal_ip is invalid")
    if not isinstance(target.get("agent_port"), int) or not 1 <= target["agent_port"] <= 65535:
        raise StackError("target.agent_port must be a TCP port")
    for key in ("module_root", "runtime_root", "log_root", "agent_modules_directory", "agent_password_file"):
        if key in target and (not isinstance(target[key], str) or not target[key].startswith("/")):
            raise StackError("target.{} must be an absolute path".format(key))
    environment_files = target.get("environment_files")
    if environment_files is not None:
        expected_environment_files = {"middleware_env", "profile", "cyclonedds", "device_env"}
        if not isinstance(environment_files, dict) or set(environment_files) != expected_environment_files:
            raise StackError("target.environment_files must contain middleware_env, profile, cyclonedds and device_env")
        for key, source_value in environment_files.items():
            source = (DEPLOYMENT_ROOT / source_value).resolve() if isinstance(source_value, str) else None
            if source is None or DEPLOYMENT_ROOT not in source.parents or not source.is_file():
                raise StackError("target.environment_files.{} must name a file inside deployment root".format(key))
    modules = value.get("modules")
    if not isinstance(modules, list) or not modules:
        raise StackError("manifest.modules must be a non-empty list")
    identifiers = set()
    for module in modules:
        validate_module(module, identifiers)
        identifiers.add(module["id"])
    return value


def target_paths(target: Mapping[str, Any]) -> Dict[str, str]:
    """Return device-specific paths, retaining Orin defaults for old manifests."""
    return {
        "module_root": target.get("module_root", "/etc/naviai/supervised-stack"),
        "runtime_root": target.get("runtime_root", "/run/naviai"),
        "log_root": target.get("log_root", "/var/log/naviai"),
        "agent_modules_directory": target.get("agent_modules_directory", "/etc/naviai/supervisor-agent/modules.d"),
        "agent_password_file": target.get("agent_password_file", "/etc/naviai/supervisor-agent/supervisor-rpc.password"),
    }


def validate_module(module: Any, identifiers: set[str]) -> None:
    if not isinstance(module, dict):
        raise StackError("each module must be an object")
    allowed = {"id", "description", "artifact", "install", "hooks", "helpers", "robot_types", "supervisor"}
    unknown = set(module) - allowed
    if unknown:
        raise StackError("module has unsupported keys: {}".format(", ".join(sorted(unknown))))
    identifier = module.get("id")
    if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier) or identifier in identifiers:
        raise StackError("module.id must be a unique lowercase identifier")
    if not isinstance(module.get("description"), str) or not module["description"]:
        raise StackError("module {} needs description".format(identifier))
    artifact = module.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) - {"type", "url", "sha256", "delivery"}:
        raise StackError("module {}.artifact is invalid".format(identifier))
    if artifact.get("type") not in {"deb", "run", "bundle"}:
        raise StackError("module {}.artifact.type must be deb, run or bundle".format(identifier))
    url = artifact.get("url")
    if not isinstance(url, str) or not url:
        raise StackError("module {}.artifact.url is required".format(identifier))
    parsed = urllib.parse.urlparse(url)
    if not (url.startswith("local://") or (parsed.scheme in {"http", "https"} and parsed.netloc)):
        raise StackError("module {}.artifact.url must use local://, http:// or https://".format(identifier))
    delivery = artifact.get("delivery", "embed")
    if delivery not in {"embed", "fetch"}:
        raise StackError("module {}.artifact.delivery must be embed or fetch".format(identifier))
    checksum = artifact.get("sha256")
    if not url.startswith("local://") and (not isinstance(checksum, str) or not SHA_RE.fullmatch(checksum)):
        raise StackError("remote module {} requires artifact.sha256".format(identifier))
    if checksum is not None and (not isinstance(checksum, str) or not SHA_RE.fullmatch(checksum)):
        raise StackError("module {}.artifact.sha256 is invalid".format(identifier))
    if artifact["type"] == "bundle" and (not url.startswith("local://") or delivery != "embed"):
        raise StackError("bundle module {} must use a local embedded directory".format(identifier))
    install = module.get("install", {})
    if not isinstance(install, dict) or set(install) - {"arguments", "handler", "disable_units", "stop_units", "install_directory"}:
        raise StackError("module {}.install is invalid".format(identifier))
    for key in ("arguments", "disable_units", "stop_units"):
        if key in install and (not isinstance(install[key], list) or not all(isinstance(item, str) for item in install[key])):
            raise StackError("module {}.install.{} must be a string list".format(identifier, key))
    if "handler" in install and not isinstance(install["handler"], str):
        raise StackError("module {}.install.handler must be a helper path".format(identifier))
    if "install_directory" in install and (not isinstance(install["install_directory"], str) or not install["install_directory"].startswith("/")):
        raise StackError("module {}.install.install_directory must be an absolute path".format(identifier))
    robot_types = module.get("robot_types", [])
    if not isinstance(robot_types, list) or not robot_types or not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in robot_types):
        if "robot_types" in module:
            raise StackError("module {}.robot_types must be a non-empty robot type list".format(identifier))
    hooks = module.get("hooks", {})
    if not isinstance(hooks, dict) or set(hooks) - {"pre_install", "post_install"}:
        raise StackError("module {}.hooks is invalid".format(identifier))
    for phase, commands in hooks.items():
        if not isinstance(commands, list) or not all(isinstance(command, str) and command for command in commands):
            raise StackError("module {}.hooks.{} must be a string list".format(identifier, phase))
    helpers = module.get("helpers", [])
    if not isinstance(helpers, list) or not all(isinstance(helper, str) and helper for helper in helpers):
        raise StackError("module {}.helpers must be a string list".format(identifier))
    supervisor = module.get("supervisor")
    if not isinstance(supervisor, dict):
        raise StackError("module {}.supervisor is required".format(identifier))
    mode = supervisor.get("mode")
    if mode not in {"managed", "external"}:
        raise StackError("module {}.supervisor.mode must be managed or external".format(identifier))
    if not isinstance(supervisor.get("port"), int) or not 1 <= supervisor["port"] <= 65535:
        raise StackError("module {}.supervisor.port must be a TCP port".format(identifier))
    if "register" in supervisor and not isinstance(supervisor["register"], bool):
        raise StackError("module {}.supervisor.register must be boolean".format(identifier))
    if mode == "managed":
        for key in ("command", "working_directory"):
            if not isinstance(supervisor.get(key), str) or not supervisor[key]:
                raise StackError("managed module {}.supervisor.{} is required".format(identifier, key))
        for key in ("source_files", "unset_environment", "prelude", "after_services", "part_of_services"):
            if key in supervisor and (not isinstance(supervisor[key], list) or not all(isinstance(item, str) for item in supervisor[key])):
                raise StackError("module {}.supervisor.{} must be a string list".format(identifier, key))
        if "service_name" in supervisor and (not isinstance(supervisor["service_name"], str) or not ID_RE.fullmatch(supervisor["service_name"])):
            raise StackError("module {}.supervisor.service_name must be a safe service identifier".format(identifier))
        if "environment" in supervisor and (not isinstance(supervisor["environment"], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in supervisor["environment"].items())):
            raise StackError("module {}.supervisor.environment must be a string map".format(identifier))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_source(url: str, expect_directory: bool = False) -> Optional[Path]:
    if not url.startswith("local://"):
        return None
    source = (DEPLOYMENT_ROOT / url[len("local://"):]).resolve()
    valid_type = source.is_dir() if expect_directory else source.is_file()
    if WORKSPACE_ROOT not in source.parents or not valid_type:
        raise StackError("local artifact is missing or outside deployment root: {}".format(url))
    return source


def cached_source(url: str, expected: Optional[str]) -> Optional[Path]:
    """Return a verified workspace dist cache for a remote artifact, if present."""
    if not expected:
        return None
    filename = Path(urllib.parse.urlparse(url).path).name
    if not filename:
        return None
    candidate = WORKSPACE_ROOT / "dist" / filename
    if candidate.is_file() and sha256(candidate).lower() == expected.lower():
        return candidate
    return None


def retrieve(url: str, destination: Path, expected: Optional[str]) -> None:
    source = local_source(url) or cached_source(url, expected)
    try:
        with (source.open("rb") if source else urllib.request.urlopen(url)) as input_file, destination.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
    except OSError as error:
        raise StackError("cannot retrieve {}: {}".format(url, error)) from error
    actual = sha256(destination)
    if expected and actual.lower() != expected.lower():
        destination.unlink(missing_ok=True)
        raise StackError("SHA256 mismatch for {}".format(url))


def header() -> bytes:
    return "\n".join((
        "#!/bin/sh", "set -eu", "archive_line=10",
        'work_dir=$(mktemp -d "${TMPDIR:-/tmp}/navi-supervised-stack.XXXXXX")',
        'cleanup() { rm -rf "$work_dir"; }', "trap cleanup EXIT HUP INT TERM",
        'tail -n +"$archive_line" "$0" | tar -xzf - -C "$work_dir"',
        'exec "$work_dir/install.sh" "$@"', "__ARCHIVE_BELOW__", "",
    )).encode("utf-8")


def write(stage: Path, relative: str, text: str, executable: bool = False) -> None:
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    target.chmod(0o755 if executable else 0o644)


def stage_environment(stage: Path, target: Mapping[str, Any]) -> None:
    """Stage the optional Pico base environment owned by a stack manifest."""
    for name, relative in target.get("environment_files", {}).items():
        source = (DEPLOYMENT_ROOT / relative).resolve()
        destination = stage / "generated" / "environment" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o644)


def module_launch(module: Mapping[str, Any]) -> str:
    runtime = module["supervisor"]
    lines = ["#!/bin/bash", "# Generated from supervised-stack manifest.", "set -eo pipefail"]
    for name in runtime.get("unset_environment", []):
        lines.append("unset {}".format(shlex.quote(name)))
    for key, value in runtime.get("environment", {}).items():
        lines.append("export {}={}".format(key, shlex.quote(value)))
    for path in runtime.get("source_files", []):
        lines.append("source {}".format(shlex.quote(path)))
    lines.extend(runtime.get("prelude", []))
    lines.append("cd {}".format(shlex.quote(runtime["working_directory"])))
    lines.append("exec /bin/bash -lc {}".format(shlex.quote(runtime["command"])))
    return "\n".join(lines) + "\n"


def module_entrypoint(module: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    runtime = module["supervisor"]
    identifier = module["id"]
    paths = target_paths(target)
    state = "{}/{}".format(paths["runtime_root"], identifier)
    log = "{}/{}".format(paths["log_root"], identifier)
    install_dir = "{}/{}".format(paths["module_root"], identifier)
    lines = [
        "#!/bin/bash", "set -euo pipefail", 'runtime_dir="{}"'.format(state), 'log_dir="{}"'.format(log),
        'config_path="${runtime_dir}/supervisord.conf"',
        'password_file={}'.format(shlex.quote(paths["agent_password_file"])),
        'install -d -m 0750 "$runtime_dir" "$log_dir"',
        'password=$(tr -d "\\r\\n" < "$password_file")',
        '[[ "$password" =~ ^[[:xdigit:]]{64}$ ]] || { echo "invalid Supervisor Agent RPC credential" >&2; exit 1; }',
        "cat > \"$config_path\" <<EOF",
        "[supervisord]", "nodaemon=true", "user=root", "logfile={}/supervisord.log".format(log), "pidfile={}/supervisord.pid".format(state), "",
        "[unix_http_server]", "file={}/supervisor.sock".format(state), "chmod=0700", "",
        "[inet_http_server]", "port={}:{}".format(target["internal_ip"], runtime["port"]), "username=agent", "password=${password}", "",
        "[rpcinterface:supervisor]", "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface", "",
        "[program:{}]".format(identifier), "command=/bin/bash {}/launch.sh".format(install_dir),
        "directory={}".format(runtime["working_directory"]), "autostart=true", "autorestart={}".format(runtime.get("autorestart", "unexpected")),
        "exitcodes={}".format(runtime.get("exitcodes", "0")), "startsecs={}".format(runtime.get("startsecs", 3)), "startretries={}".format(runtime.get("startretries", 3)),
        "stopasgroup=true", "killasgroup=true", "redirect_stderr=true", "stdout_logfile={}/{}.log".format(log, identifier),
        "stdout_logfile_maxbytes={}".format(runtime.get("log_maxbytes", "10MB")), "stdout_logfile_backups={}".format(runtime.get("log_backups", 5)),
    ]
    lines.extend(["EOF", 'chmod 0600 "$config_path"', 'exec /usr/bin/supervisord -c "$config_path"'])
    return "\n".join(lines) + "\n"


def module_service(module: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    identifier = module["id"]
    runtime = module["supervisor"]
    paths = target_paths(target)
    service_after = ["network-online.target", "{}.service".format(target["agent_service"])]
    service_after.extend("{}.service".format(item.removesuffix(".service")) for item in runtime.get("after_services", []))
    unit_lines = ["[Unit]", "Description={}".format(module["description"]), "After={}".format(" ".join(service_after)),
                  "Wants=network-online.target", "RequiresMountsFor={} {}".format(runtime["working_directory"], paths["log_root"])]
    if runtime.get("part_of_services"):
        unit_lines.append("PartOf={}".format(" ".join("{}.service".format(item.removesuffix(".service")) for item in runtime["part_of_services"])))
    return "\n".join(tuple(unit_lines) + (
        "",
        "[Service]", "Type=simple", "ExecStart=/bin/bash {}/{}/supervisor-entrypoint.sh".format(paths["module_root"], identifier),
        "Restart=on-failure", "RestartSec=3", "TimeoutStopSec={}".format(runtime.get("timeout_stop_seconds", 30)), "",
        "[Install]", "WantedBy=multi-user.target", "",
    ))


def module_service_name(module: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    return module["supervisor"].get("service_name", "navi-{}-{}-supervisor".format(target["platform"].lower(), module["id"]))


def render_install(manifest: Mapping[str, Any]) -> str:
    target = manifest["target"]
    paths = target_paths(target)
    uninstall_lines = ['if [[ "$action" == --uninstall ]]; then']
    for module in reversed(manifest["modules"]):
        identifier = module["id"]
        supervisor = module["supervisor"]
        if supervisor.get("register", True):
            uninstall_lines.append('  rm -f {}/{}.json'.format(shlex.quote(paths["agent_modules_directory"]), identifier))
        if supervisor["mode"] == "managed":
            service = module_service_name(module, target)
            uninstall_lines.extend([
                '  systemctl disable --now {}.service || true'.format(service),
                '  rm -f /etc/systemd/system/{}.service'.format(service),
                '  rm -rf {}/{}'.format(shlex.quote(paths["module_root"]), identifier),
            ])
    uninstall_lines.extend([
        '  systemctl daemon-reload',
        '  echo "Removed generated Supervisor services and registrations. Installed payload packages are retained."',
        '  exit 0',
        'fi',
    ])
    lines = [
        "#!/bin/bash", "set -euo pipefail", 'root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"', 'robot_type=""', 'action=install',
        "usage() {", "  cat <<'EOF'", "Usage: supervised stack installer -- [--robot-type TYPE] [--pretest|--verify|--info|--uninstall]", "EOF", "}",
        "while [[ $# -gt 0 ]]; do", "  case \"$1\" in", "    --) ;;", "    --robot-type) shift; robot_type=\"${1:?--robot-type needs a value}\" ;;", "    --robot-type=*) robot_type=\"${1#--robot-type=}\" ;;",
        "    --pretest|--verify|--info|--uninstall) action=\"$1\" ;;", "    -h|--help) usage; exit 0 ;;", "    *) echo \"ERROR: unknown argument: $1\" >&2; exit 2 ;;", "  esac", "  shift", "done",
        'if [[ "$action" == --info ]]; then echo "{}"; exit 0; fi'.format(manifest["name"]),
        'if [[ "$action" == --verify || "$action" == --pretest ]]; then',
        '  (cd "$root" && sha256sum -c payloads.sha256)',
        '  if [[ "$action" == --pretest ]]; then echo "Target: {} {} {}"; fi'.format(target["platform"], target["os_version"], target["architecture"]),
        '  exit 0',
        'fi',
        '[[ $EUID -eq 0 ]] || { echo "ERROR: run as root" >&2; exit 1; }',
        *uninstall_lines,
        '. /etc/os-release', '[[ "${{VERSION_ID:-}}" == "{}" ]] || {{ echo "ERROR: expected Ubuntu {}" >&2; exit 1; }}'.format(target["os_version"], target["os_version"]),
        'case "$(uname -m)" in aarch64|arm64) actual_arch=arm64 ;; x86_64|amd64) actual_arch=amd64 ;; *) echo "ERROR: unsupported architecture" >&2; exit 1;; esac',
        '[[ "$actual_arch" == "{}" ]] || {{ echo "ERROR: expected {} architecture" >&2; exit 1; }}'.format(target["architecture"], target["architecture"]),
        'command -v supervisord >/dev/null || { echo "ERROR: supervisor is required" >&2; exit 1; }',
        '(cd "$root" && sha256sum -c payloads.sha256)',
    ]
    if target.get("environment_files"):
        lines.extend([
            '[[ -n "$robot_type" ]] || { echo "ERROR: --robot-type is required to configure the Pico environment" >&2; exit 2; }',
            'install -d -m 0755 /etc/nav01 /etc/zj_humanoid /etc/profile.d',
            'install -m 0644 "$root/generated/environment/middleware_env" {}'.format(shlex.quote(target["middleware_env"])),
            'install -m 0644 "$root/generated/environment/profile" /etc/profile.d/zj_humanoid.sh',
            'install -m 0644 "$root/generated/environment/cyclonedds" /etc/zj_humanoid/cyclonedds.xml',
            'device_env=/etc/zj_humanoid/device.env',
            'if [[ -e "$device_env" && ! -e "${device_env}.pre-supervised-stack" ]]; then cp -a "$device_env" "${device_env}.pre-supervised-stack"; fi',
            'sed "s/^ROBOT_TYPE=.*/ROBOT_TYPE=${robot_type}/" "$root/generated/environment/device_env" > "${device_env}.tmp"',
            'install -m 0644 "${device_env}.tmp" "$device_env"',
            'rm -f "${device_env}.tmp"',
        ])
    lines.append('[[ -r {} ]] || {{ echo "ERROR: shared Middleware environment is missing" >&2; exit 1; }}'.format(shlex.quote(target["middleware_env"])))
    lines.extend([
        'install -d -m 0755 {} {}'.format(shlex.quote(paths["module_root"]), shlex.quote(paths["agent_modules_directory"])),
    ])
    for module in manifest["modules"]:
        if not module.get("robot_types"):
            continue
        identifier = module["id"]
        supported = " ".join(module["robot_types"])
        lines.extend([
            '[[ -n "$robot_type" ]] || {{ echo "ERROR: module {} requires --robot-type: {}" >&2; exit 2; }}'.format(identifier, supported),
            'case " {} " in *" $robot_type "*) ;; *) echo "ERROR: module {} supports only: {}" >&2; exit 2 ;; esac'.format(supported, identifier, supported),
        ])
    for module in manifest["modules"]:
        identifier = module["id"]
        install = module.get("install", {})
        artifact = module["artifact"]
        lines.append('echo "========== INSTALL {} =========="'.format(identifier.upper()))
        for unit in install.get("stop_units", []):
            lines.append("systemctl stop {} || true".format(shlex.quote(unit)))
        for unit in install.get("disable_units", []):
            lines.append("systemctl disable --now {} || true".format(shlex.quote(unit)))
        lines.extend(module.get("hooks", {}).get("pre_install", []))
        artifact_path = '"$root/payloads/{}.{}"'.format(identifier, artifact["type"])
        if artifact.get("delivery", "embed") == "fetch":
            cache = "/var/cache/naviai/supervised-stack/{}.{}".format(identifier, artifact["type"])
            lines.extend([
                'install -d -m 0755 /var/cache/naviai/supervised-stack',
                'artifact_path={}'.format(shlex.quote(cache)),
                'if [[ ! -r "$artifact_path" ]] || ! echo {}  "$artifact_path" | sha256sum -c - >/dev/null; then'.format(shlex.quote(artifact["sha256"])),
                '  tmp="${artifact_path}.tmp"; rm -f "$tmp"',
                '  python3 -c {} {} "$tmp"'.format(shlex.quote("import shutil,sys,urllib.request; shutil.copyfileobj(urllib.request.urlopen(sys.argv[1]), open(sys.argv[2], 'wb'))"), shlex.quote(artifact["url"])),
                '  echo {}  "$tmp" | sha256sum -c -; chmod 0755 "$tmp"; mv "$tmp" "$artifact_path"'.format(shlex.quote(artifact["sha256"])),
                'fi',
            ])
        arguments = [argument.replace("{robot_type}", '"$robot_type"') for argument in install.get("arguments", [])]
        if artifact["type"] == "bundle":
            install_directory = install.get("install_directory")
            if install_directory:
                lines.extend([
                    'install -d -m 0755 {}'.format(shlex.quote(install_directory)),
                    'cp -a {}/. {}/'.format(artifact_path, shlex.quote(install_directory)),
                    'artifact_path={}'.format(shlex.quote(install_directory)),
                ])
            lines.append('/bin/bash "$artifact_path/install.sh" {}'.format(" ".join(arguments)))
        elif install.get("handler"):
            lines.append('python3 "$root/helpers/{}" {} {}'.format(install["handler"], artifact_path, " ".join(arguments)))
        elif artifact["type"] == "deb":
            lines.append("dpkg -i {}".format(artifact_path))
        else:
            lines.append("/bin/bash {} {}".format(artifact_path, " ".join(arguments)))
        lines.extend(module.get("hooks", {}).get("post_install", []))
        supervisor = module["supervisor"]
        if supervisor.get("register", True):
            lines.append('install -m 0644 "$root/generated/modules/{}.json" {}/{}.json'.format(identifier, shlex.quote(paths["agent_modules_directory"]), identifier))
        if supervisor["mode"] == "managed":
            service = module_service_name(module, target)
            lines.extend([
                'install -d -m 0755 {}/{}'.format(shlex.quote(paths["module_root"]), identifier),
                'install -m 0755 "$root/generated/{}/launch.sh" {}/{}/launch.sh'.format(identifier, shlex.quote(paths["module_root"]), identifier),
                'install -m 0755 "$root/generated/{}/supervisor-entrypoint.sh" {}/{}/supervisor-entrypoint.sh'.format(identifier, shlex.quote(paths["module_root"]), identifier),
                'install -m 0644 "$root/generated/{}/{}.service" /etc/systemd/system/{}.service'.format(identifier, service, service),
                'systemctl daemon-reload', 'systemctl enable --now {}.service'.format(service),
            ])
    lines.extend([
        'systemctl restart {}.service'.format(target["agent_service"]),
        'echo "Installed. Open http://<LAN-IP>:{}"'.format(target["agent_port"]),
    ])
    return "\n".join(lines) + "\n"


def stage_helper(stage: Path, helper: str) -> None:
    source = (DEPLOYMENT_ROOT / helper).resolve()
    if DEPLOYMENT_ROOT not in source.parents or not source.is_file():
        raise StackError("helper is missing or outside deployment root: {}".format(helper))
    target = stage / "helpers" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    target.chmod(0o755)


def checksums(stage: Path) -> None:
    entries = []
    for source in sorted(stage.rglob("*")):
        if source.is_file() and source.name != "payloads.sha256":
            entries.append("{}  {}\n".format(sha256(source), source.relative_to(stage).as_posix()))
    (stage / "payloads.sha256").write_text("".join(entries), encoding="utf-8")


def build(manifest_path: Path, output_dir: Path, overrides: Optional[Mapping[str, Path]] = None) -> Path:
    manifest = load_manifest(manifest_path)
    overrides = overrides or {}
    with tempfile.TemporaryDirectory(prefix="navi-supervised-stack-build-") as temporary:
        stage = Path(temporary) / "stage"
        stage.mkdir()
        stage_environment(stage, manifest["target"])
        for module in manifest["modules"]:
            artifact = module["artifact"]
            identifier = module["id"]
            if artifact.get("delivery", "embed") == "embed":
                target = stage / "payloads" / "{}.{}".format(identifier, artifact["type"])
                target.parent.mkdir(parents=True, exist_ok=True)
                source_override = overrides.get(identifier)
                if artifact["type"] == "bundle":
                    source = source_override or local_source(artifact["url"], expect_directory=True)
                    if not source.is_dir():
                        raise StackError("override is missing: {}".format(source_override))
                    shutil.copytree(source, target)
                elif source_override is not None:
                    if not source_override.is_file():
                        raise StackError("override is missing: {}".format(source_override))
                    shutil.copy2(source_override, target)
                    if artifact.get("sha256") and sha256(target).lower() != artifact["sha256"].lower():
                        raise StackError("override checksum mismatch for {}".format(identifier))
                else:
                    retrieve(artifact["url"], target, artifact.get("sha256"))
                if artifact["type"] != "bundle":
                    target.chmod(0o755)
            handlers = list(module.get("helpers", []))
            handler = module.get("install", {}).get("handler")
            if handler:
                handlers.append(handler)
            for helper in handlers:
                stage_helper(stage, helper)
            if module["supervisor"]["mode"] == "managed":
                generated = stage / "generated" / identifier
                generated.mkdir(parents=True)
                write(generated, "launch.sh", module_launch(module), True)
                write(generated, "supervisor-entrypoint.sh", module_entrypoint(module, manifest["target"]), True)
                service = module_service_name(module, manifest["target"])
                write(generated, "{}.service".format(service), module_service(module, manifest["target"]))
            if module["supervisor"].get("register", True):
                endpoint = "http://{}:{}/RPC2".format(manifest["target"]["internal_ip"], module["supervisor"]["port"])
                write(stage, "generated/modules/{}.json".format(identifier), json.dumps({"modules": {identifier: {"endpoint": endpoint}}}, indent=2) + "\n")
        write(stage, "install.sh", render_install(manifest), True)
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
        output = build(arguments.manifest.resolve(), arguments.output_dir.resolve())
    except StackError as error:
        parser.error(str(error))
    print("Built {}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
