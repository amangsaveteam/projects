#!/usr/bin/env python3
"""Build a target-aware one-stop installer from version.json and package URLs."""
import argparse
import copy
import datetime
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
import uuid
from pathlib import Path

TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
SHA = re.compile(r"^[0-9a-fA-F]{64}$")
SERVICE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*\.service$")
ENVIRONMENT_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ROS_DISTRO = re.compile(r"^(humble|jazzy)$")
MODULE_ID = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]*$")
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
    try:
        result = subprocess.run(
            ["dpkg-deb", "-c", str(deb_path)], text=True, capture_output=True, check=True
        )
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "invalid Debian archive").strip()
        raise BuildError("{} is not a readable Debian package: {}".format(field, detail)) from error
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


def resolve_system_python_contract(values, field):
    """Validate an optional system-Python runtime requirement for a DEB."""
    if values is None:
        return None
    expected = {"user", "module", "version", "cuda"}
    if not isinstance(values, dict) or set(values) != expected:
        raise BuildError(field + ".system_python_contract must contain user, module, version and cuda")
    result = {key: require(values.get(key), field + ".system_python_contract." + key) for key in expected}
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", result["module"]):
        raise BuildError(field + ".system_python_contract.module is invalid")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", result["user"]):
        raise BuildError(field + ".system_python_contract.user is invalid")
    return result


def system_python_contract_check(contract):
    """Render a clean-environment, GPU-capable system Torch preflight."""
    if contract is None:
        return []
    code = (
        "import importlib, pathlib, sys; "
        "module=importlib.import_module(sys.argv[1]); "
        "expected_version=sys.argv[2]; expected_cuda=sys.argv[3]; "
        "actual=pathlib.Path(module.__file__).resolve(); "
        "assert module.__version__ == expected_version, module.__version__; "
        "assert module.version.cuda == expected_cuda, module.version.cuda; "
        "assert module.cuda.is_available(), 'CUDA is unavailable'; "
        "assert '/.local/' not in str(actual), actual; "
        "print('{} {} CUDA {} from {}'.format(sys.argv[1], module.__version__, module.version.cuda, actual))"
    )
    command = (
        "runuser -u {user} -- env -u PYTHONPATH -u PYTHONNOUSERSITE "
        "-u VISION_CUDNN_ROOT -u VISION_CUDNN_LIB HOME=/home/{user} "
        "/usr/bin/python3 -s -c {code} {module} {version} {cuda}"
    ).format(
        user=shlex.quote(contract["user"]), code=shlex.quote(code),
        module=shlex.quote(contract["module"]), version=shlex.quote(contract["version"]),
        cuda=shlex.quote(contract["cuda"]),
    )
    return [
        "echo 'Checking system Python contract for {} before installing Vision dependencies'".format(contract["module"]),
        "{} || {{ echo 'ERROR: required NVIDIA system Torch is absent or incompatible; do not run apt --fix-broken install until the approved Orin Torch runtime is restored.' >&2; exit 1; }}".format(command),
    ]


def resolve_run_arguments(values, field):
    """Return literal run-package arguments, with a robot-type placeholder."""
    if values is None:
        return ["--", "--robot-type", "{robot_type}"]
    if not isinstance(values, list) or not all(isinstance(value, str) and "\x00" not in value for value in values):
        raise BuildError(field + ".arguments must be a string list")
    return values


def load_delivery(urls_file, supervisor_file=None):
    """Join package and operations contracts by target and module identifier."""
    packages = load(urls_file)
    supervisor_file = supervisor_file or urls_file.with_name("supervisor.json")
    if not supervisor_file.exists():
        if any(p.get("runtime") for t in packages.get("targets", {}).values() for p in t.get("runs", [])):
            raise BuildError("runtime requires supervisor.json")
        return packages
    operations = load(supervisor_file)
    if operations.get("schema_version") != 1:
        raise BuildError("supervisor schema_version must be 1")
    result = copy.deepcopy(packages)
    for target_id, settings in operations.get("targets", {}).items():
        if target_id not in result["targets"]:
            raise BuildError("Supervisor references unknown target: " + target_id)
        target = result["targets"][target_id]
        for key in settings:
            if key not in {"supervisor", "supervisor_modules", "managed_services"}:
                raise BuildError("unsupported Supervisor target field: " + key)
            if key in target:
                raise BuildError("duplicate configuration in package and supervisor files: " + key)
        target.update(settings)
        runs = {item["name"]: item for item in target.get("runs", [])}
        if len(runs) != len(target.get("runs", [])):
            raise BuildError("duplicate package name in " + target_id)
        ports = set()
        matched = set()
        for module in target.get("supervisor_modules", []):
            name = module.get("package", module["id"])
            if name not in runs:
                raise BuildError("Supervisor module references unknown package: " + name)
            matched.add(name)
            if module["port"] in ports:
                raise BuildError("duplicate Supervisor port in " + target_id)
            ports.add(module["port"])
            package = runs[name]
            for key in ("start_policy", "remove_packages"):
                if key in module:
                    if key in package:
                        raise BuildError("duplicate install policy for " + name)
                    package[key] = module.pop(key)
            module.pop("package", None)
            runtime = package.get("runtime", {})
            if not isinstance(runtime, dict) or set(runtime) - {"environment", "source_files", "unset_environment"}:
                raise BuildError(name + ".runtime supports environment, source_files and unset_environment")
            if runtime and module["mode"] != "managed":
                raise BuildError(name + ": external runtime must be configured by its native service")
            if runtime:
                for key in ("source_files", "unset_environment"):
                    values = runtime.get(key, [])
                    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                        raise BuildError(name + ".runtime." + key + " must be a string list")
                if any(not ENVIRONMENT_KEY.fullmatch(v) for v in runtime.get("unset_environment", [])):
                    raise BuildError(name + ".runtime.unset_environment contains an invalid variable name")
                if any(not v.startswith("/") or "\x00" in v for v in runtime.get("source_files", [])):
                    raise BuildError(name + ".runtime.source_files requires absolute device paths")
                # Apply developer settings after native environments; operations
                # prelude retains the final say for platform-specific overrides.
                setup = ["unset " + " ".join(shlex.quote(v) for v in runtime["unset_environment"])] if runtime.get("unset_environment") else []
                setup += ["source " + shlex.quote(v) for v in runtime.get("source_files", [])]
                setup += ["export " + v for v in resolve_environment(runtime.get("environment"), name + ".runtime.environment")]
                module["prelude"] = setup + module.get("prelude", [])
        for package in runs.values():
            if package.get("runtime") and package["name"] not in matched:
                raise BuildError("runtime has no Supervisor module: " + package["name"])
    for target_id, target in result.get("targets", {}).items():
        if target_id not in operations.get("targets", {}) and any(p.get("runtime") for p in target.get("runs", [])):
            raise BuildError(target_id + ": runtime requires Supervisor configuration")
    return result


def resolve_run_start_policy(value, field):
    """Return how a vendor run package is started after its installation phase."""
    if value is None:
        return "vendor"
    if value not in {"vendor", "supervisor", "vision-preserve-shared"}:
        raise BuildError(field + ".start_policy must be vendor, supervisor or vision-preserve-shared")
    return value


def resolve_run_remove_packages(values, field):
    """Validate explicitly retired packages removed before a vendor run installs."""
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(value, str) and TOKEN.fullmatch(value) for value in values):
        raise BuildError(field + ".remove_packages must be a package-name list")
    return values


def render_run_command(relpath, arguments, start_policy="vendor", helper_rel=None):
    rendered = []
    for argument in arguments:
        rendered.append('"$robot_type"' if argument == "{robot_type}" else shlex.quote(argument))
    suffix = " " + " ".join(rendered) if rendered else ""
    if start_policy in {"supervisor", "vision-preserve-shared"}:
        if helper_rel is None:
            raise BuildError("a supervisor-managed run requires its installer helper")
        return 'python3 "$root/{}" "$root/{}"{}'.format(helper_rel, relpath, suffix)
    return '/bin/bash "$root/{}"{}'.format(relpath, suffix)




def supervisor_paths(target_id, value):
    """Validate the shared Supervisor Agent contract for a target."""
    if value is None:
        return None
    required = {
        "internal_ip", "agent_service", "agent_modules_directory", "agent_password_file",
        "module_root", "runtime_root", "log_root",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise BuildError(target_id + ".supervisor must contain " + ", ".join(sorted(required)))
    result = {key: require(value.get(key), target_id + ".supervisor." + key) for key in required}
    if not HOST.fullmatch(result["internal_ip"]):
        raise BuildError(target_id + ".supervisor.internal_ip is invalid")
    if not result["agent_service"].removesuffix(".service"):
        raise BuildError(target_id + ".supervisor.agent_service is invalid")
    for key in required - {"internal_ip", "agent_service"}:
        if not result[key].startswith("/"):
            raise BuildError(target_id + ".supervisor." + key + " must be an absolute path")
    return result


def supervisor_module(target_id, index, value):
    """Validate one legacy Supervisor module now owned by the one-stop config."""
    field = "{}.supervisor_modules[{}]".format(target_id, index)
    if not isinstance(value, dict):
        raise BuildError(field + " must be an object")
    allowed = {
        "id", "description", "mode", "port", "register", "service_name", "restart_service", "command",
        "working_directory", "source_files", "unset_environment", "environment", "prelude",
        "autorestart", "exitcodes", "startsecs", "startretries", "timeout_stop_seconds",
        "after_services", "part_of_services", "log_maxbytes", "log_backups", "native_rpc_config",
        "startup_priority",
    }
    unknown = set(value) - allowed
    if unknown:
        raise BuildError(field + " has unsupported keys: " + ", ".join(sorted(unknown)))
    result = dict(value)
    identifier = require(result.get("id"), field + ".id")
    if not MODULE_ID.fullmatch(identifier):
        raise BuildError(field + ".id is invalid")
    result["id"] = identifier
    if "native_rpc_config" in result:
        if result.get("mode") != "external" or not isinstance(result["native_rpc_config"], str) or not result["native_rpc_config"].startswith("/"):
            raise BuildError(field + ".native_rpc_config requires an external module and absolute path")
    result["description"] = require(result.get("description"), field + ".description")
    if result.get("mode") not in {"managed", "external"}:
        raise BuildError(field + ".mode must be managed or external")
    if not isinstance(result.get("port"), int) or not 1 <= result["port"] <= 65535:
        raise BuildError(field + ".port must be a TCP port")
    if "startup_priority" in result and (
        not isinstance(result["startup_priority"], int) or isinstance(result["startup_priority"], bool)
        or not 1 <= result["startup_priority"] <= 9999
    ):
        raise BuildError(field + ".startup_priority must be an integer from 1 to 9999")
    result.setdefault("startup_priority", 100)
    if "register" in result and not isinstance(result["register"], bool):
        raise BuildError(field + ".register must be boolean")
    if "restart_service" in result:
        if result["mode"] != "external":
            raise BuildError(field + ".restart_service is only valid for an external module")
        if not isinstance(result["restart_service"], str) or not SERVICE.fullmatch(result["restart_service"]):
            raise BuildError(field + ".restart_service is invalid")
    for key in ("source_files", "unset_environment", "prelude", "after_services", "part_of_services"):
        if key in result and (not isinstance(result[key], list) or not all(isinstance(item, str) and item for item in result[key])):
            raise BuildError(field + ".{} must be a non-empty string list".format(key))
    if "environment" in result and (not isinstance(result["environment"], dict) or not all(
        isinstance(name, str) and ENVIRONMENT_KEY.fullmatch(name) and isinstance(item, str)
        for name, item in result["environment"].items()
    )):
        raise BuildError(field + ".environment must be a string map")
    if result["mode"] == "managed":
        result["command"] = require(result.get("command"), field + ".command")
        result["working_directory"] = require(result.get("working_directory"), field + ".working_directory")
        if not result["working_directory"].startswith("/"):
            raise BuildError(field + ".working_directory must be an absolute path")
        service_name = result.get("service_name", "navi-{}-{}-supervisor".format(target_id.split("-", 1)[0], identifier))
        if not isinstance(service_name, str) or not MODULE_ID.fullmatch(service_name):
            raise BuildError(field + ".service_name is invalid")
        result["service_name"] = service_name + ".service"
    return result


def supervisor_launch_script(module):
    lines = ["#!/bin/bash", "# Generated from one_stop/package-urls.json.", "set -eo pipefail"]
    unset_names = module.get("unset_environment", [])
    if unset_names:
        lines.append("unset {}".format(" ".join(shlex.quote(name) for name in unset_names)))
    for key, value in module.get("environment", {}).items():
        lines.append("export {}={}".format(key, shlex.quote(value)))
    for path in module.get("source_files", []):
        lines.append("source {}".format(shlex.quote(path)))
    lines.extend(module.get("prelude", []))
    lines.append("cd {}".format(shlex.quote(module["working_directory"])))
    # A Supervisor program must own the long-running module process.  Replacing
    # this setup shell with the configured command keeps Supervisor's PID tied
    # to `ros2 launch` (or the configured daemon), rather than launch.sh.
    lines.append("exec {}".format(module["command"]))
    return "\n".join(lines) + "\n"


def supervisor_entrypoint_script(module, paths):
    identifier = module["id"]
    runtime_dir = "{}/{}".format(paths["runtime_root"], identifier)
    log_dir = "{}/{}".format(paths["log_root"], identifier)
    install_dir = "{}/{}".format(paths["module_root"], identifier)
    lines = [
        "#!/bin/bash", "set -euo pipefail", 'runtime_dir="{}"'.format(runtime_dir),
        'log_dir="{}"'.format(log_dir), 'config_path="${runtime_dir}/supervisord.conf"',
        "password_file={}".format(shlex.quote(paths["agent_password_file"])),
        'install -d -m 0750 "$runtime_dir" "$log_dir"',
        'install -d -m 0755 {}'.format(shlex.quote(module['working_directory'])),
        'password=$(tr -d "\\r\\n" < "$password_file")',
        '[[ "$password" == 1 || "$password" =~ ^[[:xdigit:]]{64}$ ]] || { echo "invalid Supervisor Agent RPC credential" >&2; exit 1; }',
        'cat > "$config_path" <<EOF',
        "[supervisord]", "nodaemon=true", "user=root", "logfile={}/supervisord.log".format(log_dir),
        "pidfile={}/supervisord.pid".format(runtime_dir), "", "[unix_http_server]",
        "file={}/supervisor.sock".format(runtime_dir), "chmod=0700", "", "[inet_http_server]",
        "port={}:{}".format(paths["internal_ip"], module["port"]), "username=agent", "password=${password}", "",
        "[rpcinterface:supervisor]", "supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface", "",
        "[program:{}]".format(identifier), "command=/bin/bash {}/launch.sh".format(install_dir),
        "directory={}".format(module["working_directory"]), "autostart=true",
        "autorestart={}".format(module.get("autorestart", "unexpected")),
        "exitcodes={}".format(module.get("exitcodes", "0")),
        "startsecs={}".format(module.get("startsecs", 3)),
        "startretries={}".format(module.get("startretries", 3)), "stopasgroup=true", "killasgroup=true",
        "redirect_stderr=true", "stdout_logfile={}/{}.log".format(log_dir, identifier),
        "stdout_logfile_maxbytes={}".format(module.get("log_maxbytes", "10MB")),
        "stdout_logfile_backups={}".format(module.get("log_backups", 5)), "EOF",
        'chmod 0600 "$config_path"', 'exec /usr/bin/supervisord -c "$config_path"',
    ]
    return "\n".join(lines) + "\n"


def supervisor_systemd_service(module, paths):
    after = ["network-online.target", paths["agent_service"].removesuffix(".service") + ".service"]
    after.extend(item.removesuffix(".service") + ".service" for item in module.get("after_services", []))
    lines = [
        "[Unit]", "Description={}".format(module["description"]), "After={}".format(" ".join(after)),
        "Wants=network-online.target", "RequiresMountsFor={} {}".format(module["working_directory"], paths["log_root"]),
    ]
    if module.get("part_of_services"):
        lines.append("PartOf={}".format(" ".join(item.removesuffix(".service") + ".service" for item in module["part_of_services"])))
    lines.extend((
        "", "[Service]", "Type=simple",
        "ExecStart=/bin/bash {}/{}/supervisor-entrypoint.sh".format(paths["module_root"], module["id"]),
        "Restart=on-failure", "RestartSec=3", "TimeoutStopSec={}".format(module.get("timeout_stop_seconds", 30)),
        "", "[Install]", "WantedBy=multi-user.target", "",
    ))
    return "\n".join(lines)


def stage_supervisor_modules(stage, target_id, target, checksums, dry_run):
    """Stage legacy module registrations and managed Supervisor services in the one-stop archive."""
    paths = supervisor_paths(target_id, target.get("supervisor"))
    values = target.get("supervisor_modules", [])
    if paths is None:
        if values:
            raise BuildError(target_id + ".supervisor_modules requires " + target_id + ".supervisor")
        return [], [], [], None
    if not isinstance(values, list):
        raise BuildError(target_id + ".supervisor_modules must be a list")
    modules, identifiers, startup, registrations = [], set(), [], []
    for index, value in enumerate(values):
        module = supervisor_module(target_id, index, value)
        if module["id"] in identifiers:
            raise BuildError(target_id + ".supervisor_modules ids must be unique")
        identifiers.add(module["id"])
        modules.append(module)
    for module in modules:
        identifier = module["id"]
        if module.get("register", True):
            relpath = "targets/{}/supervisor/modules/{}.json".format(target_id, identifier)
            if not dry_run:
                destination = stage / relpath
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps({"modules": {identifier: {"endpoint": "http://{}:{}/RPC2".format(paths["internal_ip"], module["port"])}}}, indent=2) + "\n", encoding="utf-8")
                checksums.append((file_sha256(destination), relpath))
            registrations.append((relpath, "{}/{}.json".format(paths["agent_modules_directory"], identifier)))
        if module["mode"] != "managed":
            continue
        base = "targets/{}/supervisor/{}".format(target_id, identifier)
        launch_rel, entrypoint_rel = base + "/launch.sh", base + "/supervisor-entrypoint.sh"
        unit_rel = base + "/" + module["service_name"]
        if not dry_run:
            directory = stage / base
            directory.mkdir(parents=True, exist_ok=True)
            launch, entrypoint, unit = stage / launch_rel, stage / entrypoint_rel, stage / unit_rel
            launch.write_text(supervisor_launch_script(module), encoding="utf-8")
            entrypoint.write_text(supervisor_entrypoint_script(module, paths), encoding="utf-8")
            unit.write_text(supervisor_systemd_service(module, paths), encoding="utf-8")
            launch.chmod(0o755); entrypoint.chmod(0o755); unit.chmod(0o644)
            checksums.extend(((file_sha256(launch), launch_rel), (file_sha256(entrypoint), entrypoint_rel), (file_sha256(unit), unit_rel)))
        startup.append((module["service_name"], launch_rel, entrypoint_rel, unit_rel, identifier))
    # Native module packages own their Supervisor configuration.  The total
    # installer only provisions the shared Agent credential and restarts the
    # declared native service afterwards so it can reread that credential.
    post_install = [
        "systemctl restart {}".format(shlex.quote(module["restart_service"]))
        for module in modules if module.get("restart_service")
    ]
    for module in modules:
        if module.get("native_rpc_config"):
            destination = paths["agent_modules_directory"].rsplit("/modules.d", 1)[0]
            post_install.insert(0, "python3 {} {} {} {}".format(
                shlex.quote(destination + "/configure_native_rpc.py"),
                shlex.quote(module["native_rpc_config"]), shlex.quote(paths["agent_password_file"]),
                shlex.quote("{}:{}".format(paths["internal_ip"], module["port"]))))
    return startup, registrations, post_install, paths


def supervisor_service_priorities(target_id, target):
    """Return service priorities derived from the Supervisor module contract."""
    priorities = {}
    for index, value in enumerate(target.get("supervisor_modules", [])):
        module = supervisor_module(target_id, index, value)
        service = module.get("service_name") if module["mode"] == "managed" else module.get("restart_service")
        if service:
            if service in priorities and priorities[service] != module["startup_priority"]:
                raise BuildError(target_id + ": one service has conflicting startup priorities")
            priorities[service] = module["startup_priority"]
    return priorities


def stage_supervisor_agent(stage, target_id, paths, checksums, dry_run):
    """Stage the local Agent whenever a target declares a Supervisor contract."""
    if paths is None:
        return None
    device = target_id.split("-", 1)[0]
    if device not in {"orin", "pico"}:
        raise BuildError(target_id + ".supervisor is unsupported for this device")
    source_root = DEPLOYMENT_ROOT / "packages" / "supervisor-agent"
    module_config = source_root / "resources" / (device + "-modules.json")
    initializer = source_root / "scripts" / ("initialize_orin_agent_secrets.py" if device == "orin" else "initialize_agent_secrets.py")
    assets = {
        "navi_supervisor_agent.py": source_root / "resources" / "navi_supervisor_agent.py",
        "modules.json": module_config,
        "initialize_agent_secrets.py": initializer,
        "configure_native_rpc.py": source_root / "scripts/configure_native_rpc.py",
    }
    base = "targets/{}/supervisor-agent".format(target_id)
    if not dry_run:
        for name, source in assets.items():
            if not source.is_file():
                raise BuildError("Supervisor Agent source is missing: {}".format(source))
            destination = stage / base / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if name == "modules.json" and target_id == "orin-jazzy":
                config = json.loads(destination.read_text(encoding="utf-8"))
                config["modules"] = {}
                config["remote_agents"] = {}
                destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            destination.chmod(0o755 if name.endswith(".py") else 0o644)
            checksums.append((file_sha256(destination), (Path(base) / name).as_posix()))
        service = stage / base / (paths["agent_service"].removesuffix(".service") + ".service")
        service.write_text("\n".join((
            "[Unit]", "Description=Navi {} Supervisor Agent".format(device.title()), "After=network-online.target",
            *( ["Conflicts=navi-supervisor-agent.service", "After=navi-supervisor-agent.service"] if device == "orin" else [] ),
            "Wants=network-online.target", "", "[Service]", "Type=simple",
            "ExecStart=/usr/bin/python3 {}/navi_supervisor_agent.py --config {}/modules.json".format(
                paths["agent_modules_directory"].rsplit("/modules.d", 1)[0],
                paths["agent_modules_directory"].rsplit("/modules.d", 1)[0],
            ),
            "ExecStartPost=/usr/bin/python3 {0}/navi_supervisor_agent.py --config {0}/modules.json --wait-ready".format(
                paths["agent_modules_directory"].rsplit("/modules.d", 1)[0]
            ),
            "TimeoutStartSec=40",
            "Restart=on-failure", "RestartSec=3", "", "[Install]", "WantedBy=multi-user.target", "",
        )), encoding="utf-8")
        service.chmod(0o644)
        checksums.append((file_sha256(service), (Path(base) / service.name).as_posix()))
    return {
        "base": base,
        "service": paths["agent_service"].removesuffix(".service") + ".service",
        "destination": paths["agent_modules_directory"].rsplit("/modules.d", 1)[0],
        "replaces_services": ["navi-supervisor-agent.service"] if device == "orin" else [],
    }


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
    lines = ["#!/bin/sh", "set -eu", "archive_line=10", "work_dir=$(mktemp -d \"${TMPDIR:-/tmp}/navi-one-stop.XXXXXX\")", "cleanup() { rm -rf \"$work_dir\"; }", "trap cleanup EXIT; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM", "tail -n +\"$archive_line\" \"$0\" | tar -xzf - -C \"$work_dir\"", "\"$work_dir/install.sh\" \"$@\"; exit 0", "__ARCHIVE_BELOW__", ""]
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


def target_install(target_id, system_config_rel, common_rel, common, extras, runs, services,
                   startup_services=(), supervisor_startup=(), registrations=(), post_install=(), agent_service=None,
                   agent_payload=None, target_platform=None, release_tracking=False, service_priorities=None):
    # Native modules are also part of the installation lifecycle, even when
    # their units come from a DEB instead of a Middleware-format run archive.
    native_services = [
        shlex.split(command)[2] for command in post_install
        if command.startswith("systemctl restart ") and len(shlex.split(command)) == 3
    ]
    services = sorted(set(services) | set(native_services))
    service_priorities = service_priorities or {}
    unknown_priorities = set(service_priorities) - set(services)
    if unknown_priorities:
        raise BuildError(target_id + ": startup priority references unmanaged service: " +
                         ", ".join(sorted(unknown_priorities)))
    services = sorted(services, key=lambda service: (service_priorities.get(service, 100), service))
    lines = ["#!/bin/bash", "set -euo pipefail", "umask 022", "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"", "robot_type=\"$1\"", "(cd \"$root\" && sha256sum -c \"targets/{}/payloads.sha256\")".format(target_id)]
    if target_platform is not None:
        expected_os, expected_version, expected_arch = target_platform
        lines.extend((
            "[[ -r /etc/os-release ]] || { echo 'ERROR: /etc/os-release is missing' >&2; exit 2; }",
            ". /etc/os-release",
            "case \"$(uname -m)\" in x86_64) actual_arch=amd64 ;; aarch64|arm64) actual_arch=arm64 ;; *) actual_arch=unknown ;; esac",
            "if [[ \"${{ID,,}}\" != {} || \"${{VERSION_ID:-}}\" != {} || \"$actual_arch\" != {} ]]; then".format(
                shlex.quote(expected_os), shlex.quote(expected_version), shlex.quote(expected_arch)
            ),
            "  echo {} >&2".format(shlex.quote(
                "ERROR: {} payloads require {} {} / {}; refusing to install on this device.".format(
                    target_id, expected_os, expected_version, expected_arch
                )
            )),
            "  exit 2",
            "fi",
        ))
    if release_tracking:
        lines.extend([
            "install -d -m 0755 /var/lib/naviai/release",
            "exec 9>/var/lib/naviai/release/install.lock",
            "flock -n 9 || { echo 'ERROR: another release installation is running' >&2; exit 1; }",
            "install -d -m 0755 /usr/local/bin",
            'install -m 0755 "$root/release_state.py" /usr/local/bin/navi-version',
            'navi_release() { /usr/bin/python3 "$root/release_state.py" "$@"; }',
            'navi_release begin --manifest "$root/targets/{}/release-manifest.json" --robot-type "$robot_type"'.format(target_id),
        ])
    if services:
        lines.extend([
            "managed_services=(" + " ".join('\"{}\"'.format(item) for item in services) + ")",
            "stop_managed_services() {", "  local unit failed=0", "  for unit in \"${managed_services[@]}\"; do",
            "    if systemctl is-active --quiet \"$unit\"; then", "      echo \"Stopping $unit\"", "      systemctl stop \"$unit\" || failed=1", "    fi",
            "  done", "  return \"$failed\"", "}", "install_complete=0", "on_install_exit() {", "  status=$?",
            "  if [[ \"$install_complete\" -ne 1 ]]; then", "    echo \"ERROR: overall installation failed; managed services are being kept stopped.\" >&2",
            "    stop_managed_services || true",
            *(['    navi_release fail || true'] if release_tracking else []),
            "  fi", "  exit \"$status\"", "}", "trap on_install_exit EXIT", "stop_managed_services",
        ])
    elif release_tracking:
        lines.extend([
            "install_complete=0",
            'on_install_exit() { status=$?; if [[ "$install_complete" -ne 1 ]]; then navi_release fail || true; fi; exit "$status"; }',
            "trap on_install_exit EXIT",
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
    # The vendor Robot unit chdirs before ExecStartPre, so its mkdir cannot
    # create its own missing WorkingDirectory on a freshly provisioned host.
    if target_id == "orin-humble":
        lines.append("install -d -m 0755 /var/lib/navi /var/lib/navi/ros /var/log/navi/ros /var/log/navi/robot /var/log/naviai/robot")
    for relpath, installers, environment, contract in extras:
        lines.extend(system_python_contract_check(contract))
        lines.append("dpkg -i \"$root/{}\"".format(relpath))
        prefix = "env " + " ".join(environment) + " " if environment else ""
        lines.extend(prefix + "\"{}\"".format(item) for item in installers)
    for run in runs:
        item, arguments = run[:2]
        start_policy = run[2] if len(run) > 2 else "vendor"
        helper_rel = run[3] if len(run) > 3 else None
        remove_packages = run[4] if len(run) > 4 else []
        for package in remove_packages:
            lines.extend((
                "if dpkg-query -W -f='${{db:Status-Status}}' {} 2>/dev/null | grep -Fxq installed; then".format(shlex.quote(package)),
                "  echo {}".format(shlex.quote(
                    "Removing retired package {} before installing its replacement.".format(package)
                )),
                "  dpkg --remove {}".format(shlex.quote(package)),
                "fi",
            ))
        environment = run[5] if len(run) > 5 else []
        prefix = "env " + " ".join(environment) + " " if environment else ""
        lines.append(prefix + render_run_command(item, arguments, start_policy, helper_rel))
        if services:
            lines.append("stop_managed_services")
    for _, launch_rel, service_rel, destination in startup_services:
        lines.extend((
            "install -d -m 0755 {}".format(shlex.quote(str(Path(destination).parent))),
            "install -m 0755 \"$root/{}\" {}".format(launch_rel, shlex.quote(destination)),
            "install -m 0644 \"$root/{}\" /etc/systemd/system/{}".format(service_rel, shlex.quote(Path(service_rel).name)),
        ))
    for service, launch_rel, entrypoint_rel, service_rel, identifier in supervisor_startup:
        module_root = Path(agent_service["module_root"]) / identifier
        lines.extend((
            "install -d -m 0755 {}".format(shlex.quote(str(module_root))),
            "install -m 0755 \"$root/{}\" {}".format(launch_rel, shlex.quote(str(module_root / "launch.sh"))),
            "install -m 0755 \"$root/{}\" {}".format(entrypoint_rel, shlex.quote(str(module_root / "supervisor-entrypoint.sh"))),
            "install -m 0644 \"$root/{}\" /etc/systemd/system/{}".format(service_rel, shlex.quote(service)),
        ))
    if agent_payload:
        agent_base = agent_payload["base"]
        destination = agent_payload["destination"]
        for retired_service in agent_payload.get("replaces_services", []):
            lines.extend((
                "if [[ $(systemctl show -p LoadState --value {}) != not-found ]]; then".format(shlex.quote(retired_service)),
                "  systemctl disable --now {}".format(shlex.quote(retired_service)),
                "fi",
            ))
        lines.extend((
            "install -d -m 0755 {}".format(shlex.quote(destination)),
            "install -m 0755 \"$root/{}/navi_supervisor_agent.py\" {}/navi_supervisor_agent.py".format(agent_base, shlex.quote(destination)),
            "install -m 0644 \"$root/{}/modules.json\" {}/modules.json".format(agent_base, shlex.quote(destination)),
            "install -m 0755 \"$root/{}/initialize_agent_secrets.py\" {}/initialize_agent_secrets.py".format(agent_base, shlex.quote(destination)),
            "python3 {}/initialize_agent_secrets.py".format(shlex.quote(destination)),
            "install -m 0755 \"$root/{}/configure_native_rpc.py\" {}/configure_native_rpc.py".format(agent_base, shlex.quote(destination)),
            "install -m 0644 \"$root/{}/{}\" /etc/systemd/system/{}".format(agent_base, agent_payload["service"], shlex.quote(agent_payload["service"])),
        ))
    if registrations:
        lines.append("install -d -m 0755 {}".format(shlex.quote(agent_service["agent_modules_directory"])))
        lines.extend("install -m 0644 \"$root/{}\" {}".format(relpath, shlex.quote(destination)) for relpath, destination in registrations)
    # Restart declared native units once, in the same final pass as generated
    # units, after new unit files and shared credentials have been installed.
    lines.extend(command for command in post_install if not (
        command.startswith("systemctl restart ") and len(shlex.split(command)) == 3
    ))
    if services:
        lines.extend([
            "systemctl daemon-reload", "for unit in \"${managed_services[@]}\"; do",
            "  echo \"Starting $unit after lower-priority services are active\"",
            "  systemctl enable \"$unit\"", "  systemctl restart \"$unit\"",
            "  systemctl is-active --quiet \"$unit\" || { echo \"ERROR: $unit did not become active\" >&2; exit 1; }",
            "done",
        ])
    if agent_payload:
        lines.extend((
            "systemctl daemon-reload", "systemctl enable {}".format(shlex.quote(agent_payload["service"])),
            "systemctl restart {}".format(shlex.quote(agent_payload["service"])),
        ))
    if release_tracking:
        lines.append("navi_release complete")
    if services or release_tracking:
        lines.extend(("install_complete=1", "trap - EXIT"))
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
    for relpath, _, _, contract in extras:
        if contract:
            lines.append("echo 'Required system Python: {} {} / CUDA {}'".format(contract["module"], contract["version"], contract["cuda"]))
        lines.append("pretest_deb \"$root/{}\"".format(relpath))
    for run in runs:
        relpath = run[0]
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


def build(version_file, urls_file, output_dir, dry_run=False, supervisor_file=None):
    if supervisor_file is not None and not supervisor_file.is_file():
        raise BuildError("Supervisor configuration not found: " + str(supervisor_file))
    version_data, urls_data = load(version_file), load_delivery(urls_file, supervisor_file)
    if version_data.get("schema_version") != 1 or urls_data.get("schema_version") != 1:
        raise BuildError("schema_version must be 1")
    version = require(version_data.get("version"), "version", safe=True)
    output_name = require(version_data.get("output_name"), "output_name", safe=True)
    built_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    def git_value(*arguments):
        try:
            result = subprocess.run(["git", "-C", str(DEPLOYMENT_ROOT), *arguments],
                                    capture_output=True, text=True)
        except FileNotFoundError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None
    release_identity = {
        "schema_version": 1, "release": version,
        "build_id": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12],
        "built_at": built_at, "git_commit": git_value("rev-parse", "HEAD"),
        "git_dirty": bool(git_value("status", "--porcelain")),
        "version_config_sha256": file_sha256(version_file),
        "package_config_sha256": file_sha256(urls_file),
        "supervisor_config_sha256": file_sha256(supervisor_file or urls_file.with_name("supervisor.json"))
            if (supervisor_file or urls_file.with_name("supervisor.json")).is_file() else None,
        "builder_sha256": file_sha256(Path(__file__)),
        "offline_installation": "unverified",
    }
    raw_targets = urls_data.get("targets")
    if not isinstance(raw_targets, dict):
        raise BuildError("targets must be an object")
    with tempfile.TemporaryDirectory(prefix="navi-one-stop-") as temporary:
        stage, rows, checksums = Path(temporary) / "stage", [], []
        manifests = {}
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
            artifacts = []
            def record_artifact(item, relpath, kind, index):
                if dry_run:
                    return
                declared_version = item.get("version")
                if declared_version is not None:
                    require(declared_version, target_id + ".artifact.version")
                artifacts.append({
                    "name": item.get("name") or "{}-{}".format(kind, index),
                    "kind": kind, "version": declared_version,
                    "version_source": "configuration" if declared_version else "unknown",
                    "url": item["url"], "path": relpath,
                    "sha256": file_sha256(stage / relpath),
                })
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
                record_artifact(common, common_rel, "common", 0)
            extras, runs = [], []
            configured_services = target.get("managed_services", [])
            if not isinstance(configured_services, list) or any(not isinstance(item, str) or not SERVICE.fullmatch(item) for item in configured_services):
                raise BuildError(target_id + ".managed_services must be a list of systemd unit names")
            services = list(configured_services)
            if "vision_supervisor" in target:
                raise BuildError(target_id + ": use supervisor_modules for Vision; standalone vision_supervisor is no longer supported")
            startup_services = []
            supervisor_startup, registrations, supervisor_post_install, supervisor_config = stage_supervisor_modules(
                stage, target_id, target, target_checksums, dry_run
            )
            service_priorities = supervisor_service_priorities(target_id, target)
            supervisor_agent = stage_supervisor_agent(stage, target_id, supervisor_config, target_checksums, dry_run)
            services.extend(item[0] for item in supervisor_startup)
            for index, item in enumerate(target.get("extra_debs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/extra-{:02d}.deb".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".extra.url"), path, expected, dry_run)
                if not dry_run: target_checksums.append((file_sha256(path), relpath))
                record_artifact(item, relpath, "dependency", index)
                extras.append((
                    relpath,
                    resolve_installers(path, item.get("installers", []), target_id + ".extra.installers", dry_run),
                    resolve_environment(item.get("environment"), target_id + ".extra.environment"),
                    resolve_system_python_contract(item.get("system_python_contract"), target_id + ".extra"),
                ))
            requires_no_final_exec_helper = False
            for index, item in enumerate(target.get("runs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/run-{:02d}.run".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".run.url"), path, expected, dry_run)
                record_artifact(item, relpath, "module", index)
                if not dry_run:
                    path.chmod(0o755)
                    target_checksums.append((file_sha256(path), relpath))
                    services.extend(services_from_run(path))
                start_policy = resolve_run_start_policy(item.get("start_policy"), target_id + ".run")
                requires_no_final_exec_helper |= start_policy == "supervisor"
                vision_helper_rel = "targets/{}/helpers/install_vision_preserving_shared.py".format(target_id)
                if start_policy == "vision-preserve-shared" and not dry_run:
                    helper = stage / vision_helper_rel
                    helper.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(__file__).resolve().parent / "install_vision_preserving_shared.py", helper)
                    target_checksums.append((file_sha256(helper), vision_helper_rel))
                runs.append((
                    relpath,
                    resolve_run_arguments(item.get("arguments"), target_id + ".run"),
                    start_policy,
                    "targets/{}/helpers/install_run_without_final_exec.py".format(target_id) if start_policy == "supervisor" else (vision_helper_rel if start_policy == "vision-preserve-shared" else None),
                    resolve_run_remove_packages(item.get("remove_packages"), target_id + ".run"),
                    resolve_environment(item.get("environment"), target_id + ".run.environment"),
                ))
            if requires_no_final_exec_helper and not dry_run:
                helper_source = Path(__file__).resolve().parent / "install_run_without_final_exec.py"
                helper_rel = "targets/{}/helpers/install_run_without_final_exec.py".format(target_id)
                helper = stage / helper_rel
                helper.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(helper_source, helper)
                helper.chmod(0o755)
                target_checksums.append((file_sha256(helper), helper_rel))
            if not dry_run:
                helper = stage / "release_state.py"
                shutil.copy2(Path(__file__).with_name("release_state.py"), helper)
                target_checksums.append((file_sha256(helper), "release_state.py"))
                modules = {}
                for artifact in artifacts:
                    if artifact["kind"] == "module":
                        if artifact["name"] in modules:
                            raise BuildError("duplicate module name: " + artifact["name"])
                        modules[artifact["name"]] = artifact
                manifest = dict(release_identity, target=target_id, robot_type=None,
                                platform={"os_id": os_id, "os_version": os_version, "architecture": arch},
                                modules=modules, artifacts=artifacts,
                                payload_checksums=dict((path, value) for value, path in target_checksums))
                manifests[target_id] = manifest
                manifest_rel = "targets/{}/release-manifest.json".format(target_id)
                manifest_path = stage / manifest_rel
                manifest_path.parent.mkdir(parents=True, exist_ok=True)
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                target_checksums.append((file_sha256(manifest_path), manifest_rel))
            script = stage / "targets" / target_id / "install.sh"; script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text(
                target_install(
                    target_id, system_config_rel, common_rel, common, extras, runs, sorted(set(services)),
                    startup_services, supervisor_startup, registrations, supervisor_post_install, supervisor_config,
                    supervisor_agent, (os_id, os_version, arch), release_tracking=True,
                    service_priorities=service_priorities,
                ), encoding="utf-8"
            ); script.chmod(0o755)
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
        (stage / "release-manifest.json").write_text(
            json.dumps(dict(release_identity, targets=manifests), indent=2) + "\n", encoding="utf-8")
        checksums.append((file_sha256(stage / "release-manifest.json"), "release-manifest.json"))
        (stage / "payloads.sha256").write_text("".join("{}  {}\n".format(value, path) for value, path in sorted(checksums)), encoding="utf-8")
        install = stage / "install.sh"; install.write_text(master_install(rows, version), encoding="utf-8"); install.chmod(0o755)
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".navi-one-stop-output-", dir=output_dir) as output_workspace:
            temporary_output = Path(output_workspace) / output.name
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
    parser.add_argument("--supervisor", type=Path, help="operations configuration (defaults to supervisor.json beside package URLs)")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[2] / "dist"); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try: build(args.version.resolve(), args.urls.resolve(), args.output_dir.resolve(), args.dry_run,
               args.supervisor.resolve() if args.supervisor else None)
    except BuildError as error: print("ERROR: {}".format(error), file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
