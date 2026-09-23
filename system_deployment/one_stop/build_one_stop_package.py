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
import urllib.error
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
    candidates = [url]
    # Only substitute known repository roots when the manifest pins the bytes.
    if expected:
        for prefix, mirrors in (
            ("https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/", (
                "https://ports.ubuntu.com/ubuntu-ports/",
                "https://mirrors.ustc.edu.cn/ubuntu-ports/")),
            ("https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu/", (
                "https://repo.huaweicloud.com/ros2/ubuntu/",
                "https://mirrors.ustc.edu.cn/ros2/ubuntu/",
                "https://packages.ros.org/ros2/ubuntu/")),
        ):
            if url.startswith(prefix):
                candidates.extend(root + url[len(prefix):] for root in mirrors)
    last_error = None
    for candidate in candidates:
        print("Download {}".format(candidate))
        request = urllib.request.Request(candidate, headers={"User-Agent": "Mozilla/5.0"})
        for attempt in range(1, retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=120) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
                if expected and file_sha256(destination) != expected.lower():
                    raise BuildError("SHA256 mismatch for {}".format(candidate))
                return
            except (OSError, BuildError) as error:
                last_error = error
                if isinstance(error, BuildError) or (
                    isinstance(error, urllib.error.HTTPError) and error.code in (403, 404)
                ):
                    break
                if attempt < retries:
                    print("  retry {}/{} for {}: {}".format(attempt, retries, candidate, error))
                    time.sleep(2 * attempt)
        print("  download rejected or exhausted: {}".format(last_error))
        destination.unlink(missing_ok=True)
    raise BuildError("download failed for {}: {}".format(url, last_error)) from last_error


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


def resolve_install_group(value, field):
    """Return an optional contiguous dpkg transaction name."""
    if value is None:
        return None
    return require(value, field + ".install_group", safe=True)


def resolve_wait_packages(values, field):
    """Return Debian packages which must be installed before services start."""
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(value, str) and TOKEN.fullmatch(value) for value in values):
        raise BuildError(field + ".wait_for_packages must be a Debian package-name list")
    return values


def resolve_skip_if_package_installed(value, field):
    """Validate an optional installed-package guard for a transitional DEB."""
    if value is None:
        return None
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise BuildError(field + ".skip_if_package_installed must be a Debian package name")
    return value


def load_delivery(urls_file, supervisor_file=None):
    """Join package and operations contracts by target and module identifier."""
    packages = load(urls_file)
    supervisor_file = supervisor_file or urls_file.with_name("supervisor.json")
    if not supervisor_file.exists():
        if any(p.get("runtime") for t in packages.get("targets", {}).values()
               for p in t.get("runs", []) + t.get("extra_debs", [])):
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
        debs = {item["name"]: item for item in target.get("extra_debs", [])}
        module_packages = dict(debs)
        module_packages.update(runs)
        if len(runs) != len(target.get("runs", [])) or len(debs) != len(target.get("extra_debs", [])):
            raise BuildError("duplicate package name in " + target_id)
        ports = set()
        matched = set()
        for module in target.get("supervisor_modules", []):
            explicit_package = "package" in module
            name = module.get("package", module["id"])
            if name not in module_packages:
                raise BuildError("Supervisor module references unknown package: " + name)
            if explicit_package and name in runs and name in debs:
                raise BuildError("Supervisor module package is ambiguous; use a distinct DEB name: " + name)
            matched.add(name)
            # Keep the resolved package identity for the release manifest.
            # ``package`` itself belongs only to the source Supervisor file.
            module["_package_name"] = name
            if module.get("mode") in {"managed", "external"}:
                port = module.get("port")
                if port in ports:
                    raise BuildError("duplicate Supervisor port in " + target_id)
                ports.add(port)
            package = module_packages[name]
            for key in ("start_policy", "remove_packages"):
                if key in module:
                    if name not in runs:
                        raise BuildError(key + " is supported only for RUN package: " + name)
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
        for package in module_packages.values():
            if package.get("runtime") and package["name"] not in matched:
                raise BuildError("runtime has no Supervisor module: " + package["name"])
    for target_id, target in result.get("targets", {}).items():
        if target_id not in operations.get("targets", {}) and any(
            p.get("runtime") for p in target.get("runs", []) + target.get("extra_debs", [])
        ):
            raise BuildError(target_id + ": runtime requires Supervisor configuration")
    return result


def resolve_run_start_policy(value, field):
    """Return how a vendor run package is started after its installation phase."""
    if value is None:
        return "vendor"
    if value not in {"vendor", "supervisor", "vision-preserve-shared", "robot-verify-fix"}:
        raise BuildError(field + ".start_policy must be vendor, supervisor, vision-preserve-shared or robot-verify-fix")
    return value


def resolve_run_remove_packages(values, field):
    """Validate explicitly retired packages removed before a vendor run installs."""
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(value, str) and TOKEN.fullmatch(value) for value in values):
        raise BuildError(field + ".remove_packages must be a package-name list")
    return values


def resolve_force_overwrite(value, field):
    """Validate the narrowly scoped opt-in for a known legacy file conflict."""
    if value is None:
        return False
    if value is not True:
        raise BuildError(field + ".force_overwrite must be true when specified")
    return True


def resolve_robot_types(values, field):
    """Validate an optional robot-model allowlist for a delivery item."""
    if values is None:
        return []
    if (not isinstance(values, list) or not values or
            not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in values)):
        raise BuildError(field + ".robot_types must be a non-empty robot-type list")
    if len(set(values)) != len(values):
        raise BuildError(field + ".robot_types must not contain duplicates")
    return values


def robot_type_condition(robot_types):
    if not robot_types:
        return None
    return '[[ " {} " == *" $robot_type "* ]]'.format(" ".join(robot_types))


def conditional_lines(robot_types, lines):
    condition = robot_type_condition(robot_types)
    if condition is None:
        return lines
    return ["if {}; then".format(condition), *("  " + line for line in lines), "fi"]


def render_run_command(relpath, arguments, start_policy="vendor", helper_rel=None):
    rendered = []
    for argument in arguments:
        rendered.append('"$robot_type"' if argument == "{robot_type}" else shlex.quote(argument))
    suffix = " " + " ".join(rendered) if rendered else ""
    if start_policy in {"supervisor", "vision-preserve-shared", "robot-verify-fix"}:
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
        "id", "description", "mode", "port", "register", "service_name", "restart_service", "systemd_service", "command", "_package_name",
        "working_directory", "source_files", "unset_environment", "environment", "prelude",
        "autorestart", "exitcodes", "startsecs", "startretries", "timeout_stop_seconds", "readiness_command", "local_log_file",
        "after_services", "part_of_services", "disable_services", "log_maxbytes", "log_backups", "native_rpc_config",
        "startup_priority", "robot_types",
    }
    unknown = set(value) - allowed
    if unknown:
        raise BuildError(field + " has unsupported keys: " + ", ".join(sorted(unknown)))
    result = dict(value)
    result["robot_types"] = resolve_robot_types(result.get("robot_types"), field)
    identifier = require(result.get("id"), field + ".id")
    if not MODULE_ID.fullmatch(identifier):
        raise BuildError(field + ".id is invalid")
    result["id"] = identifier
    if "native_rpc_config" in result:
        if result.get("mode") != "external" or not isinstance(result["native_rpc_config"], str) or not result["native_rpc_config"].startswith("/"):
            raise BuildError(field + ".native_rpc_config requires an external module and absolute path")
    result["description"] = require(result.get("description"), field + ".description")
    if result.get("mode") not in {"managed", "external", "systemd"}:
        raise BuildError(field + ".mode must be managed, external or systemd")
    if result["mode"] == "systemd":
        if not isinstance(result.get("systemd_service"), str) or not SERVICE.fullmatch(result["systemd_service"]):
            raise BuildError(field + ".systemd_service must be a systemd unit name")
        if any(key in result for key in ("port", "restart_service", "native_rpc_config", "command", "working_directory")):
            raise BuildError(field + ".systemd must not define RPC or managed-process fields")
    elif not isinstance(result.get("port"), int) or not 1 <= result["port"] <= 65535:
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
    for key in ("source_files", "unset_environment", "prelude", "after_services", "part_of_services", "disable_services"):
        if key in result and (not isinstance(result[key], list) or not all(isinstance(item, str) and item for item in result[key])):
            raise BuildError(field + ".{} must be a non-empty string list".format(key))
    if result.get("disable_services") and result["mode"] not in {"managed", "external"}:
        raise BuildError(field + ".disable_services is only valid for a managed or external module")
    if "disable_services" in result and any(not SERVICE.fullmatch(item) for item in result["disable_services"]):
        raise BuildError(field + ".disable_services must contain systemd unit names")
    if "environment" in result and (not isinstance(result["environment"], dict) or not all(
        isinstance(name, str) and ENVIRONMENT_KEY.fullmatch(name) and isinstance(item, str)
        for name, item in result["environment"].items()
    )):
        raise BuildError(field + ".environment must be a string map")
    if "readiness_command" in result:
        if result["mode"] != "managed" or not isinstance(result["readiness_command"], str) or not result["readiness_command"].strip():
            raise BuildError(field + ".readiness_command requires a managed module and must be non-empty")
    if "local_log_file" in result:
        if result["mode"] != "managed" or not isinstance(result["local_log_file"], str) or not result["local_log_file"].startswith("/"):
            raise BuildError(field + ".local_log_file requires a managed module and an absolute path")
    if result["mode"] == "managed":
        result["command"] = require(result.get("command"), field + ".command")
        result["working_directory"] = require(result.get("working_directory"), field + ".working_directory")
        if not result["working_directory"].startswith("/"):
            raise BuildError(field + ".working_directory must be an absolute path")
        service_name = result.get(
            "service_name", "zj-humanoid-{}-{}-supervisor".format(target_id.split("-", 1)[0], identifier)
        )
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
    if module.get("readiness_command"):
        lines.extend((
            "while ! ({}); do".format(module["readiness_command"]),
            "  echo 'Waiting for robot to publish STATE_ROBOT_RUN before starting this module.' >&2",
            "  sleep 2",
            "done",
        ))
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
        'if ss -H -ltnp "sport = :{}" | grep -q .; then'.format(module["port"]),
        '  echo "ERROR: Supervisor RPC port {} is already in use; refusing to start a second instance." >&2'.format(module["port"]),
        '  ss -ltnp "sport = :{}" >&2 || true'.format(module["port"]),
        '  exit 1',
        'fi',
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
                specification = ({"type": "systemd", "service": module["systemd_service"]}
                                 if module["mode"] == "systemd" else
                                 {"endpoint": "http://{}:{}/RPC2".format(paths["internal_ip"], module["port"])})
                if module.get("local_log_file"):
                    specification["local_log_file"] = module["local_log_file"]
                destination.write_text(json.dumps({"modules": {identifier: specification}}, indent=2) + "\n", encoding="utf-8")
                checksums.append((file_sha256(destination), relpath))
            registrations.append((relpath, "{}/{}.json".format(paths["agent_modules_directory"], identifier), module["robot_types"]))
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
        startup.append((module["service_name"], launch_rel, entrypoint_rel, unit_rel, identifier, module["robot_types"]))
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
        service = (module.get("service_name") if module["mode"] == "managed"
                   else module.get("restart_service") if module["mode"] == "external"
                   else module.get("systemd_service"))
        if service:
            if service in priorities and priorities[service] != module["startup_priority"]:
                raise BuildError(target_id + ": one service has conflicting startup priorities")
            priorities[service] = module["startup_priority"]
    return priorities


def supervisor_systemd_services(target_id, target):
    """Return native systemd units exposed through the local Agent."""
    return sorted({supervisor_module(target_id, index, value)["systemd_service"]
                   for index, value in enumerate(target.get("supervisor_modules", []))
                   if supervisor_module(target_id, index, value)["mode"] == "systemd"})


def supervisor_disabled_services(target_id, target):
    """Return vendor systemd units retired in favour of configured Supervisors."""
    disabled = {
        service
        for index, value in enumerate(target.get("supervisor_modules", []))
        for service in supervisor_module(target_id, index, value).get("disable_services", [])
    }
    # Release 2.0.0-2 and earlier generated navi-<platform>-* units.  Retire
    # them before installing the zj-humanoid-* replacement so an upgraded
    # device has one owner for each Supervisor port and business process.
    platform = target_id.split("-", 1)[0]
    for index, value in enumerate(target.get("supervisor_modules", [])):
        module = supervisor_module(target_id, index, value)
        if module["mode"] == "managed":
            legacy = "navi-{}-{}-supervisor.service".format(platform, module["id"])
            if legacy != module["service_name"]:
                disabled.add(legacy)
    return sorted(disabled)


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
        legacy_agents = ["navi-{}-supervisor-agent.service".format(device)]
        if device == "orin":
            legacy_agents.append("navi-supervisor-agent.service")
        service = stage / base / (paths["agent_service"].removesuffix(".service") + ".service")
        service.write_text("\n".join((
            "[Unit]", "Description=Navi {} Supervisor Agent".format(device.title()), "After=network-online.target",
            "Conflicts=" + " ".join(legacy_agents),
            "After=" + " ".join(legacy_agents),
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
        "replaces_services": (
            ["navi-{}-supervisor-agent.service".format(device)]
            + (["navi-supervisor-agent.service"] if device == "orin" else [])
        ),
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
    lines = ["#!/bin/sh", "set -eu", "archive_line=10", "work_dir=$(mktemp -d \"${TMPDIR:-/tmp}/navi-one-stop.XXXXXX\")", "cleanup() { rm -rf \"$work_dir\"; }", "trap cleanup EXIT; trap 'exit 129' HUP; trap 'exit 130' INT; trap 'exit 143' TERM", "tail -n +\"$archive_line\" \"$0\" | tar -xzf - -C \"$work_dir\"", "status=0; \"$work_dir/install.sh\" \"$@\" || status=$?; exit \"$status\"", "__ARCHIVE_BELOW__", ""]
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


def stage_config_files(stage, target_id, target, checksums, dry_run):
    """Bundle repository directories; deploy after module installers, preserving backups."""
    lines = ["#!/bin/bash", "set -euo pipefail",
             'root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"',
             'robot_type="${1:?robot type is required}"']
    for index, item in enumerate(target.get("config_files", [])):
        field = "{}.config_files[{}]".format(target_id, index)
        if not isinstance(item, dict) or set(item) - {"source", "destination", "owner", "group", "overwrite", "robot_types"}:
            raise BuildError(field + " contains unsupported fields")
        robot_types = resolve_robot_types(item.get("robot_types"), field)
        source_value = item.get("source")
        destination_value = item.get("destination")
        if not isinstance(source_value, str) or not isinstance(destination_value, str):
            raise BuildError(field + ".source and destination must be strings")
        overwrite = item.get("overwrite", True)
        if not isinstance(overwrite, bool):
            raise BuildError(field + ".overwrite must be true or false")
        source = (DEPLOYMENT_ROOT.parent / source_value).resolve()
        destination = Path(destination_value)
        if not source.is_dir() or DEPLOYMENT_ROOT.parent.resolve() not in source.parents:
            raise BuildError("config_files source must be a repository directory: " + str(source))
        if not destination.is_absolute() or ".." in destination.parts or len(destination.parts) < 4:
            raise BuildError("config_files destination must be a specific absolute directory")
        owner = item.get("owner", "root")
        group = item.get("group", owner)
        if not TOKEN.fullmatch(owner) or not TOKEN.fullmatch(group):
            raise BuildError("invalid config_files owner/group")
        for file in sorted(source.rglob("*")):
            if file.is_symlink():
                raise BuildError("config_files does not accept symlinks: " + str(file))
            if not file.is_file():
                continue
            relative = file.relative_to(source)
            payload = Path("payloads") / target_id / "config-files" / str(index) / relative
            dst = destination / relative
            if not dry_run:
                output = stage / payload
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, output)
                checksums.append((file_sha256(output), payload.as_posix()))
            file_lines = ["install -d -o {} -g {} -m 0755 {}".format(
                owner, group, shlex.quote(str(dst.parent)))]
            if overwrite:
                file_lines += [
                    "cp --backup=numbered -- \"$root/{}\" {}".format(payload.as_posix(), shlex.quote(str(dst))),
                    "chown {}:{} -- {}".format(owner, group, shlex.quote(str(dst))),
                ]
            else:
                file_lines += [
                    "if [[ ! -e {} ]]; then".format(shlex.quote(str(dst))),
                    "  install -o {} -g {} -m 0644 \"$root/{}\" {}".format(
                        owner, group, payload.as_posix(), shlex.quote(str(dst))),
                    "else",
                    "  echo \"Keeping local configuration: {}\"".format(dst),
                    "fi",
                ]
            lines.extend(conditional_lines(robot_types, file_lines))
    if not dry_run:
        script = stage / "targets" / target_id / "install-config-files.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("\n".join(lines) + "\n", encoding="utf-8")


def stage_system_config(stage, target_id, target, checksums, dry_run):
    stage_config_files(stage, target_id, target, checksums, dry_run)
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
                   agent_payload=None, target_platform=None, release_tracking=False, service_priorities=None,
                   disabled_services=(), remove_packages=(), service_robot_types=None,
                   required_host_packages=()):
    # Native modules are also part of the installation lifecycle, even when
    # their units come from a DEB instead of a Middleware-format run archive.
    native_services = [
        shlex.split(command)[2] for command in post_install
        if command.startswith("systemctl restart ") and len(shlex.split(command)) == 3
    ]
    services = sorted(set(services) | set(native_services))
    service_robot_types = service_robot_types or {}
    service_priorities = service_priorities or {}
    unknown_priorities = set(service_priorities) - set(services)
    if unknown_priorities:
        raise BuildError(target_id + ": startup priority references unmanaged service: " +
                         ", ".join(sorted(unknown_priorities)))
    services = sorted(services, key=lambda service: (service_priorities.get(service, 100), service))
    lines = [
        "#!/bin/bash", "set -Eeuo pipefail", "umask 022",
        "root=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/../..\" && pwd)\"", "robot_type=\"$1\"",
        "install_stage='verifying payload checksums'",
        "on_install_error() {", "  local status=$1 command=$2",
        "  echo \"ERROR: installation failed during ${install_stage:-an unknown stage} (exit $status): $command\" >&2", "}",
        "trap 'on_install_error \"$?\" \"$BASH_COMMAND\"' ERR",
        "wait_for_dpkg_lock() {",
        "  local retries=600 lock holder",
        "  while (( retries > 0 )); do",
        "    holder=''",
        "    for lock in /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/cache/apt/archives/lock; do",
        "      if [[ -e \"$lock\" ]] && fuser -s \"$lock\" 2>/dev/null; then holder=$lock; break; fi",
        "    done",
        "    [[ -z $holder ]] && return 0",
        "    echo \"Waiting for package manager lock held by: $(fuser \"$holder\" 2>/dev/null || true)\" >&2",
        "    sleep 1",
        "    retries=$((retries - 1))",
        "  done",
        "  echo 'ERROR: timed out waiting for the package manager lock' >&2",
        "  return 1",
        "}",
        "run_package_command() {",
        "  local attempt=1 output status",
        "  while (( attempt <= 600 )); do",
        "    wait_for_dpkg_lock || return 1",
        "    output=$(mktemp)",
        "    if \"$@\" 2>&1 | tee \"$output\"; then status=0; else status=${PIPESTATUS[0]}; fi",
        "    if (( status == 0 )); then rm -f \"$output\"; return 0; fi",
        "    if grep -Fq 'dpkg frontend lock was locked by another process' \"$output\" || grep -Fq 'dpkg database lock was locked by another process' \"$output\"; then",
        "      rm -f \"$output\"",
        "      echo \"Waiting for package manager lock before retrying package command ($attempt/600)\" >&2",
        "      sleep 1; attempt=$((attempt + 1)); continue",
        "    fi",
        "    rm -f \"$output\"; return \"$status\"",
        "  done",
        "  echo 'ERROR: timed out retrying package command after package manager lock conflicts' >&2",
        "  return 2",
        "}",
        "run_module_installer() { run_package_command \"$@\"; }", 
        "(cd \"$root\" && sha256sum -c \"targets/{}/payloads.sha256\")".format(target_id),
    ]
    if target_platform is not None:
        expected_os, expected_version, expected_arch = target_platform
        lines.append("install_stage='checking target platform'")
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
    if required_host_packages:
        lines.extend([
            "install_stage='checking required host packages'",
            "missing_host_packages=()",
            "for package in " + " ".join(shlex.quote(package) for package in required_host_packages) + "; do",
            "  if ! dpkg-query -W -f='${db:Status-Status}' \"$package\" 2>/dev/null | grep -Fxq installed; then",
            "    missing_host_packages+=(\"$package\")",
            "  fi",
            "done",
            'if (( ${#missing_host_packages[@]} )); then',
            '  echo "ERROR: base image is missing required packages: ${missing_host_packages[*]}. Install them before retrying; no services have been stopped." >&2',
            '  exit 1',
            'fi',
        ])
    if release_tracking:
        lines.extend([
            "install_stage='initializing release tracking'",
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
    if remove_packages:
        lines.append("install_stage='removing retired compatibility packages'")
        for package in remove_packages:
            lines.extend((
                "if dpkg-query -W -f='${{db:Status-Status}}' {} 2>/dev/null | grep -Fxq installed; then".format(shlex.quote(package)),
                "  echo {}".format(shlex.quote("Removing retired compatibility package {}.".format(package))),
                "  run_package_command dpkg --remove {}".format(shlex.quote(package)),
                "fi",
            ))
    if system_config_rel:
        middleware_env = "/etc/nav01/Middleware.env" if target_id.startswith("pico-") else "/etc/naviai/Middleware.env"
        lines.extend([
            "install_system_config() {",
            "  /bin/bash \"$root/targets/{}/install-system-config.sh\" \"$robot_type\"".format(target_id),
            "}",
            "load_shared_middleware() {",
            "  [[ -r {} ]] || {{ echo {} >&2; return 1; }}".format(
                shlex.quote(middleware_env),
                shlex.quote("ERROR: shared Middleware environment is missing: " + middleware_env),
            ),
            "  source {}".format(shlex.quote(middleware_env)),
            "}",
            "install_stage='installing shared system configuration'",
            "install_system_config",
        ])
    elif isinstance(common, dict):
        tool = require(common.get("configure_tool"), "common.configure_tool")
        configure_target = require(common.get("configure_target"), "common.configure_target", safe=True)
        installer = require(common.get("installer"), "common.installer")
        lines.extend(["install_stage='installing common dependencies'", "dpkg -i \"$root/{}\"".format(common_rel), "python3 \"{}\" configure --target \"{}\" --robot-type \"$robot_type\"".format(tool, configure_target), "\"{}\"".format(installer)])
    else:
        raise BuildError(target_id + " must configure system_config or common")
    # The vendor Robot unit chdirs before ExecStartPre, so its mkdir cannot
    # create its own missing WorkingDirectory on a freshly provisioned host.
    if target_id == "orin-humble":
        lines.append("install -d -m 0755 /var/lib/navi /var/lib/navi/ros /var/log/navi/ros /var/log/navi/robot /var/log/naviai/robot")
    if disabled_services:
        lines.extend([
            "disable_replaced_services() {",
            "  local unit",
            "  for unit in " + " ".join(shlex.quote(unit) for unit in sorted(set(disabled_services))) + "; do",
            "    if [[ $(systemctl show -p LoadState --value \"$unit\") != not-found ]]; then",
            "      echo \"Disabling vendor service replaced by Supervisor: $unit\"",
            "      systemctl disable --now \"$unit\"",
            "    fi",
            "  done",
            "}",
            "disable_replaced_services",
        ])
    if any(len(extra) > 5 and extra[5] for extra in extras):
        lines.extend([
            "wait_for_debian_package() {",
            "  local package=$1 retries=120",
            "  while (( retries > 0 )); do",
            "    if dpkg-query -W -f='${db:Status-Status}' \"$package\" 2>/dev/null | grep -Fxq installed; then return 0; fi",
            "    sleep 1",
            "    retries=$((retries - 1))",
            "  done",
            "  echo \"ERROR: timed out waiting for Debian package $package\" >&2",
            "  return 1",
            "}",
        ])
    extra_groups, seen_groups = [], set()
    for extra in extras:
        relpath, installers, environment, contract = extra[:4]
        group = extra[4] if len(extra) > 4 else None
        wait_packages = extra[5] if len(extra) > 5 else []
        skip_if_installed = extra[6] if len(extra) > 6 else None
        force_overwrite = extra[7] if len(extra) > 7 else False
        robot_types = extra[8] if len(extra) > 8 else []
        # An omitted group preserves the historical one-DEB-at-a-time
        # behaviour.  Only a named group forms a shared dpkg transaction.
        if group is None:
            extra_groups.append((None, [(relpath, installers, environment, contract, wait_packages, skip_if_installed, force_overwrite, robot_types)]))
            continue
        if skip_if_installed:
            raise BuildError(target_id + ": skip_if_package_installed cannot be used with install_group")
        if force_overwrite:
            raise BuildError(target_id + ": force_overwrite requires an extra_debs entry without install_group")
        if group and group in seen_groups and (not extra_groups or extra_groups[-1][0] != group):
            raise BuildError(target_id + ": install_group entries must be contiguous: " + group)
        if not extra_groups or extra_groups[-1][0] != group:
            extra_groups.append((group, []))
        extra_groups[-1][1].append((relpath, installers, environment, contract, wait_packages, skip_if_installed, force_overwrite, robot_types))
        if group:
            seen_groups.add(group)
    for group, items in extra_groups:
        robot_type_sets = {tuple(item[7]) for item in items}
        if len(robot_type_sets) != 1:
            raise BuildError(target_id + ": install_group must use one robot_types allowlist: " + str(group))
        condition = robot_type_condition(items[0][7])
        if condition:
            lines.append("if {}; then".format(condition))
        install_stage = "installing Debian package group {}".format(group) if group else "installing Debian package {}".format(Path(items[0][0]).name)
        lines.append("install_stage=" + shlex.quote(install_stage))
        environments = {tuple(item[2]) for item in items}
        if len(environments) != 1:
            raise BuildError(target_id + ": install_group must use one installation environment: " + str(group))
        environment = items[0][2]
        prefix = "env " + " ".join(environment) + " " if environment else ""
        for _, _, _, contract, *_ in items:
            lines.extend(system_python_contract_check(contract))
        if group is None and items[0][5]:
            relpath, installers, _, _, _, skip_if_installed, force_overwrite, _ = items[0]
            if force_overwrite:
                raise BuildError(target_id + ": force_overwrite cannot be combined with skip_if_package_installed")
            lines.extend((
                "if dpkg-query -W -f='${{db:Status-Status}}' {} 2>/dev/null | grep -Fxq installed; then".format(shlex.quote(skip_if_installed)),
                "  echo {}".format(shlex.quote(
                    "Keeping installed {} instead of replacing it with the transitional compatibility package.".format(skip_if_installed)
                )),
                "else",
                "  run_package_command " + prefix + "dpkg -i \"$root/{}\"".format(relpath),
                *("  run_package_command /bin/bash -c " + shlex.quote(prefix + item) for item in installers),
                "fi",
            ))
            if condition:
                lines.append("fi")
            continue
        if group is None and items[0][6]:
            relpath, installers, _, _, _, _, _, _ = items[0]
            lines.append("run_package_command " + prefix + "dpkg --force-overwrite -i \"$root/{}\"".format(relpath))
            lines.extend("run_package_command /bin/bash -c " + shlex.quote(prefix + item) for item in installers)
            if condition:
                lines.append("fi")
            continue
        paths = " ".join('\"$root/{}\"'.format(item[0]) for item in items)
        lines.append("run_package_command " + prefix + "dpkg -i " + paths)
        for _, installers, _, _, _, _, _, _ in items:
            lines.extend("run_package_command /bin/bash -c " + shlex.quote(prefix + item) for item in installers)
        for package in sorted({package for _, _, _, _, packages, *_ in items for package in packages}):
            lines.append("wait_for_debian_package {}".format(shlex.quote(package)))
        if condition:
            lines.append("fi")
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
                "  run_package_command dpkg --remove {}".format(shlex.quote(package)),
                "fi",
            ))
        environment = run[5] if len(run) > 5 else []
        robot_types = run[6] if len(run) > 6 else []
        condition = robot_type_condition(robot_types)
        if condition:
            lines.append("if {}; then".format(condition))
        prefix = "env " + " ".join(environment) + " " if environment else ""
        lines.append("install_stage=" + shlex.quote("running module installer " + Path(item).name))
        lines.append("wait_for_dpkg_lock")
        if system_config_rel:
            # A vendor installer may alter Middleware.env (the Pico upperlimb
            # installer currently does).  Load the known-good carrier before
            # each installer and restore it immediately afterwards so all
            # following installers and generated services share one ROS/DDS
            # identity.
            lines.append("load_shared_middleware")
        module_command = "run_module_installer " + prefix + render_run_command(item, arguments, start_policy, helper_rel)
        lines.extend((
            "if " + module_command + "; then",
            "  :",
            "else",
            "  status=$?",
            "  echo \"ERROR: module installer {} failed (exit $status)\" >&2".format(Path(item).name),
            "  exit \"$status\"",
            "fi",
        ))
        if system_config_rel:
            lines.extend(("install_system_config", "load_shared_middleware"))
        if services:
            lines.append("stop_managed_services")
        if condition:
            lines.append("fi")
    # Vendor RUN packages may enable or start their own units while installing.
    # Retire them again before the generated Supervisor units claim the same
    # port and business process.
    if disabled_services:
        lines.append("disable_replaced_services")
    lines.extend((
        "install_stage='installing configuration files'",
        'if [[ -f "$root/targets/{0}/install-config-files.sh" ]]; then bash "$root/targets/{0}/install-config-files.sh" "$robot_type"; fi'.format(target_id),
    ))
    for _, launch_rel, service_rel, destination in startup_services:
        lines.extend((
            "install -d -m 0755 {}".format(shlex.quote(str(Path(destination).parent))),
            "install -m 0755 \"$root/{}\" {}".format(launch_rel, shlex.quote(destination)),
            "install -m 0644 \"$root/{}\" /etc/systemd/system/{}".format(service_rel, shlex.quote(Path(service_rel).name)),
        ))
    for service, launch_rel, entrypoint_rel, service_rel, identifier, robot_types in supervisor_startup:
        install_lines = (
            "install -d -m 0755 {}".format(shlex.quote(str(Path(agent_service["module_root"]) / identifier))),
            "install -m 0755 \"$root/{}\" {}".format(launch_rel, shlex.quote(str(Path(agent_service["module_root"]) / identifier / "launch.sh"))),
            "install -m 0755 \"$root/{}\" {}".format(entrypoint_rel, shlex.quote(str(Path(agent_service["module_root"]) / identifier / "supervisor-entrypoint.sh"))),
            "install -m 0644 \"$root/{}\" /etc/systemd/system/{}".format(service_rel, shlex.quote(service)),
        )
        condition = robot_type_condition(robot_types)
        if condition:
            lines.append("if {}; then".format(condition))
        lines.extend(install_lines)
        if condition:
            lines.append("fi")
    if agent_payload:
        lines.append("install_stage='installing Supervisor Agent'")
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
    if agent_service:
        modules_directory = agent_service["agent_modules_directory"]
        declared_registrations = [item[1] for item in registrations]
        lines.extend((
            "modules_directory={}".format(shlex.quote(modules_directory)),
            "declared_modules=(" + " ".join(shlex.quote(item) for item in declared_registrations) + ")",
            "prune_stale_module_registrations() {",
            "  local module_file declared",
            "  for module_file in \"$modules_directory\"/*.json; do",
            "    [[ -e \"$module_file\" ]] || continue",
            "    for declared in \"${declared_modules[@]}\"; do",
            "      [[ \"$module_file\" == \"$declared\" ]] && continue 2",
            "    done",
            "    rm -f -- \"$module_file\"",
            "  done",
            "}",
            "install -d -m 0755 \"$modules_directory\"",
            "prune_stale_module_registrations",
        ))
        for registration in registrations:
            relpath, destination = registration[:2]
            robot_types = registration[2] if len(registration) > 2 else []
            condition = robot_type_condition(robot_types)
            if condition:
                lines.append("if {}; then".format(condition))
            lines.append("install -m 0644 \"$root/{}\" {}".format(relpath, shlex.quote(destination)))
            if condition:
                lines.append("fi")
        lines.append("prune_stale_module_registrations")
    # Restart declared native units once, in the same final pass as generated
    # units, after new unit files and shared credentials have been installed.
    lines.extend(command for command in post_install if not (
        command.startswith("systemctl restart ") and len(shlex.split(command)) == 3
    ))
    if services:
        lines.extend([
            "install_stage='starting managed services'",
            "systemctl daemon-reload", "for unit in \"${managed_services[@]}\"; do",
            "  case \"$unit\" in",
        ])
        for service, robot_types in sorted(service_robot_types.items()):
            condition = robot_type_condition(robot_types)
            if condition:
                lines.append("    {}) if ! {}; then continue; fi ;;".format(service, condition))
        lines.extend([
            "  esac",
            "  echo \"Starting $unit after lower-priority services are active\"",
            "  systemctl enable \"$unit\"", "  systemctl restart \"$unit\"",
            "  systemctl is-active --quiet \"$unit\" || { echo \"ERROR: $unit did not become active\" >&2; exit 1; }",
            "done",
        ])
    if agent_payload:
        lines.extend((
            "install_stage='starting Supervisor Agent'",
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
    for extra in extras:
        relpath, _, _, contract = extra[:4]
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
        "target=\"\"", "robot_type=\"\"", "robot_type_source=\"\"", "device_robot_type=\"\"", "robot_type_error=\"\"", "confirm_robot_type_change=0", "action=install",
        "while [[ $# -gt 0 ]]; do", "  case \"$1\" in", "    --) ;;",
        "    --target) shift; target=\"${1:?--target needs a value}\" ;;", "    --target=*) target=\"${1#--target=}\" ;;",
        "    --robot-type) shift; robot_type=\"${1:?--robot-type needs a value}\"; robot_type_source=argument ;;", "    --robot-type=*) robot_type=\"${1#--robot-type=}\"; robot_type_source=argument ;;",
        "    --confirm-robot-type-change) confirm_robot_type_change=1 ;;",
        "    --list-targets|--info|--verify|--pretest) action=\"$1\" ;;", "    -h|--help) echo \"Usage: $0 [--target TARGET] [--robot-type TYPE] [--confirm-robot-type-change] [--pretest]\"; exit 0 ;;",
        "    *) echo \"ERROR: unknown argument: $1\" >&2; exit 2 ;;", "  esac", "  shift", "done",
        "resolve_robot_type() {", "  if [[ -r /etc/zj_humanoid/device.env ]]; then", "    device_robot_type=$(python3 - /etc/zj_humanoid/device.env <<'PY'", "import re", "import sys", "from pathlib import Path", "assignment = re.compile(r'^\\s*ROBOT_TYPE\\s*=\\s*(?:\\\"([^\\\"]*)\\\"|\\\'([^\\\']*)\\\'|([^\\s#;]+))\\s*(?:#.*)?$')", "value = ''", "for line in Path(sys.argv[1]).read_text(encoding='utf-8').splitlines():", "    match = assignment.match(line)", "    if match:", "        value = next(item for item in match.groups() if item is not None)", "if value and not value.startswith('${') and re.fullmatch(r'[A-Za-z0-9_-]+', value):", "    print(value)", "elif value:", "    raise SystemExit(1)", "PY", "    ) || { robot_type_error=\"invalid ROBOT_TYPE in /etc/zj_humanoid/device.env\"; return; }", "  fi",
        "  if [[ -n \"$robot_type\" ]]; then", "    [[ \"$robot_type\" =~ ^[A-Za-z0-9_-]+$ ]] || { robot_type_error=\"invalid --robot-type: $robot_type\"; return; }", "    return", "  fi",
        "  if [[ -n \"$device_robot_type\" ]]; then robot_type=$device_robot_type; robot_type_source=device.env; fi", "}",
        "validate_robot_type_change() {", "  [[ \"$target\" == pico-* ]] || return 0", "  [[ \"$robot_type_source\" == argument ]] || { echo \"ERROR: Pico installation requires explicit --robot-type TYPE; refusing to reuse or overwrite /etc/zj_humanoid/device.env.\" >&2; return 1; }", "  if [[ -n \"$device_robot_type\" && \"$device_robot_type\" != \"$robot_type\" && \"$confirm_robot_type_change\" != 1 ]]; then", "    echo \"ERROR: existing ROBOT_TYPE=$device_robot_type differs from requested $robot_type. Refusing to change device identity without --confirm-robot-type-change.\" >&2; return 1", "  fi", "}",
        "table=$'{}'".format(table), "if [[ \"$action\" == --list-targets ]]; then printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi",
        "if [[ \"$action\" == --info ]]; then echo \"Version: {}\"; printf '%s\\n' \"$table\" | tr '|' '\\t'; exit 0; fi".format(version),
        "if [[ \"$action\" == --verify ]]; then (cd \"$root\" && sha256sum -c payloads.sha256); exit $?; fi",
        "if [[ -z \"$target\" ]]; then", "  os_id=\"\"; os_version=\"\"", "  [[ -r /etc/os-release ]] && . /etc/os-release && os_id=\"${ID:-}\" && os_version=\"${VERSION_ID:-}\"",
        "  case \"$(uname -m)\" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; *) echo \"ERROR: unsupported architecture\" >&2; exit 2 ;; esac",
        "  while IFS='|' read -r candidate candidate_os candidate_version candidate_arch; do", "    if [[ \"${os_id,,}\" == \"$candidate_os\" && \"$os_version\" == \"$candidate_version\" && \"$arch\" == \"$candidate_arch\" ]]; then target=\"$candidate\"; break; fi", "  done <<< \"$table\"", "fi",
        "[[ -n \"$target\" && -x \"$root/targets/$target/install.sh\" ]] || { echo \"ERROR: target unavailable: $target\" >&2; exit 2; }",
        "resolve_robot_type", "if [[ \"$action\" == --pretest ]]; then", "  if [[ -n \"$robot_type\" ]]; then echo \"Robot type: $robot_type ($robot_type_source)\"; else echo \"Robot type: not configured; bare device installation requires --robot-type TYPE\"; fi", "  [[ -z \"$robot_type_error\" ]] || echo \"WARN: $robot_type_error\" >&2", "  exec \"$root/targets/$target/pretest.sh\"", "fi",
        "[[ $EUID -eq 0 ]] || { echo \"ERROR: run as root\" >&2; exit 1; }", "[[ -z \"$robot_type_error\" ]] || { echo \"ERROR: $robot_type_error\" >&2; exit 2; }", "validate_robot_type_change || exit 2", "[[ -n \"$robot_type\" ]] || { echo \"ERROR: bare device requires --robot-type TYPE\" >&2; exit 2; }", "echo \"Existing ROBOT_TYPE: ${device_robot_type:-<none>}\"", "echo \"Requested ROBOT_TYPE: $robot_type\"",
        "echo \"Robot type: $robot_type ($robot_type_source)\"", "exec \"$root/targets/$target/install.sh\" \"$robot_type\"", "",
    ]
    return "\n".join(lines)


def validate_staged_shell_scripts(stage):
    """Reject malformed generated scripts before publishing an installer."""
    for script in sorted((stage / "targets").rglob("*.sh")):
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        if result.returncode:
            raise BuildError("invalid shell script {}: {}".format(
                script.relative_to(stage), result.stderr.strip()))


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
            supervisor_package_names = {
                module.get("_package_name", module["id"])
                for module in target.get("supervisor_modules", [])
            }
            service_priorities = supervisor_service_priorities(target_id, target)
            services.extend(supervisor_systemd_services(target_id, target))
            service_robot_types = {
                module["service_name"]: module["robot_types"]
                for index, value in enumerate(target.get("supervisor_modules", []))
                for module in [supervisor_module(target_id, index, value)]
                if module["mode"] == "managed"
            }
            disabled_services = supervisor_disabled_services(target_id, target)
            remove_packages = resolve_run_remove_packages(target.get("remove_packages"), target_id + ".remove_packages")
            supervisor_agent = stage_supervisor_agent(stage, target_id, supervisor_config, target_checksums, dry_run)
            services.extend(item[0] for item in supervisor_startup)
            for index, item in enumerate(target.get("extra_debs", [])):
                if not isinstance(item, dict) or not item.get("url"): continue
                relpath = "payloads/{}/extra-{:02d}.deb".format(target_id, index); path = stage / relpath; path.parent.mkdir(parents=True, exist_ok=True)
                expected = str(item.get("sha256", "")); download(require(item["url"], target_id + ".extra.url"), path, expected, dry_run)
                if not dry_run: target_checksums.append((file_sha256(path), relpath))
                record_artifact(item, relpath,
                                "module" if item.get("name") in supervisor_package_names else "dependency", index)
                extras.append((
                    relpath,
                    resolve_installers(path, item.get("installers", []), target_id + ".extra.installers", dry_run),
                    resolve_environment(item.get("environment"), target_id + ".extra.environment"),
                    resolve_system_python_contract(item.get("system_python_contract"), target_id + ".extra"),
                    resolve_install_group(item.get("install_group"), target_id + ".extra"),
                    resolve_wait_packages(item.get("wait_for_packages"), target_id + ".extra"),
                    resolve_skip_if_package_installed(item.get("skip_if_package_installed"), target_id + ".extra"),
                    resolve_force_overwrite(item.get("force_overwrite"), target_id + ".extra"),
                    resolve_robot_types(item.get("robot_types"), target_id + ".extra"),
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
                    # A module configured as managed replaces any vendor unit
                    # with the same role; do not bring that retired unit back
                    # during the final service-start pass.
                    services.extend(service for service in services_from_run(path)
                                    if service not in disabled_services)
                start_policy = resolve_run_start_policy(item.get("start_policy"), target_id + ".run")
                requires_no_final_exec_helper |= start_policy == "supervisor"
                vision_helper_rel = "targets/{}/helpers/install_vision_preserving_shared.py".format(target_id)
                if start_policy == "vision-preserve-shared" and not dry_run:
                    helper = stage / vision_helper_rel
                    helper.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(__file__).resolve().parent / "install_vision_preserving_shared.py", helper)
                    target_checksums.append((file_sha256(helper), vision_helper_rel))
                robot_helper_rel = "targets/{}/helpers/install_robot_with_verify_fix.py".format(target_id)
                if start_policy == "robot-verify-fix" and not dry_run:
                    helper = stage / robot_helper_rel
                    helper.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(Path(__file__).resolve().parent / "install_robot_with_verify_fix.py", helper)
                    target_checksums.append((file_sha256(helper), robot_helper_rel))
                runs.append((
                    relpath,
                    resolve_run_arguments(item.get("arguments"), target_id + ".run"),
                    start_policy,
                    "targets/{}/helpers/install_run_without_final_exec.py".format(target_id) if start_policy == "supervisor" else (vision_helper_rel if start_policy == "vision-preserve-shared" else (robot_helper_rel if start_policy == "robot-verify-fix" else None)),
                    resolve_run_remove_packages(item.get("remove_packages"), target_id + ".run"),
                    resolve_environment(item.get("environment"), target_id + ".run.environment"),
                    resolve_robot_types(item.get("robot_types"), target_id + ".run"),
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
                    service_priorities=service_priorities, disabled_services=disabled_services,
                    remove_packages=remove_packages, service_robot_types=service_robot_types,
                    required_host_packages=resolve_run_remove_packages(
                        target.get("required_host_packages"), target_id + ".required_host_packages"),
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
        validate_staged_shell_scripts(stage)
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
