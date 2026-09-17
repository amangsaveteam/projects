#!/usr/bin/env python3
"""Read-only installation checks. Copy this file alone to the target device."""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess
import urllib.request
import urllib.parse
import xmlrpc.client


TARGETS = {
    ("ubuntu", "22.04", "aarch64"): "orin-humble",
    ("ubuntu", "24.04", "aarch64"): "orin-jazzy",
    ("ubuntu", "20.04", "x86_64"): "pico-humble",
    ("ubuntu", "24.04", "x86_64"): "pico-jazzy",
    ("rdk os", "V5.1.0", "aarch64"): "rdk-jazzy",
}
MODULES = {
    "orin-humble": {
        "manip-segmentation": ("zj-humanoid-orin-manip-segmentation-supervisor.service", 19013, True),
        "manip-sam6d": ("zj-humanoid-orin-manip-sam6d-supervisor.service", 19014, True),
        "manip-lingbot": ("zj-humanoid-orin-manip-lingbot-supervisor.service", 19015, True),
        "manip-hand-detect": ("zj-humanoid-orin-manip-hand-detect-supervisor.service", 19016, True),
        "sensor": ("navi-sensor-host.service", 19001, False),
        "robot": ("zj-humanoid-orin-robot-supervisor.service", 19002, True),
        "audio": ("zj-humanoid-orin-audio-supervisor.service", 19003, True),
        "chassis": ("zj-humanoid-orin-chassis-supervisor.service", 19004, True),
        "vanjee-lidar": ("zj-humanoid-orin-vanjee-lidar-supervisor.service", 19006, True),
        "livox-lidar": ("zj-humanoid-orin-livox-lidar-supervisor.service", 19007, True),
        "naviai-nav2": ("zj-humanoid-orin-naviai-nav2-supervisor.service", 19008, True),
        "navigation": ("zj-humanoid-orin-navigation-supervisor.service", 19009, True),
        "naviai-nav2-rawdata": ("zj-humanoid-orin-naviai-nav2-rawdata-supervisor.service", 19010, True),
        "diagnosis-system": ("zj-humanoid-orin-diagnosis-system-supervisor.service", 19011, True),
        "web-rviz": ("zj-humanoid-orin-web-rviz-supervisor.service", 19012, True),
        "vision": ("zj-humanoid-orin-vision-supervisor.service", 19005, True),
    },
    "orin-jazzy": {"vision": ("zj-humanoid-orin-vision-supervisor.service", 19005, True)},
    "pico-humble": {
        "robot": ("navi-pico-robot-supervisor.service", 19002, False),
        "upperlimb": ("navi-pico-upperlimb.service", 19003, False),
        "display": ("zj-humanoid-pico-display-supervisor.service", 19004, True),
    },
}


def key_values(path):
    return key_values_from_text(Path(path).read_text())


def key_values_from_text(text):
    return dict(line.split("=", 1) for line in text.splitlines()
                if "=" in line and not line.lstrip().startswith("#"))


class Checks:
    def __init__(self, timeout):
        self.rows = []
        self.timeout = timeout

    def report(self, name, state, detail=""):
        self.rows.append({"check": name, "state": state, "detail": detail})

    def check(self, name, action):
        try:
            detail = action()
            self.report(name, "PASS", str(detail or "OK"))
        except Exception as error:
            self.report(name, "FAIL", str(error))

    def command(self, arguments):
        result = subprocess.run(arguments, text=True, capture_output=True, timeout=self.timeout)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "exit {}".format(result.returncode)).strip())
        return result.stdout.strip()

    def file(self, path):
        def inspect():
            value = Path(path).read_bytes()
            if not value:
                raise ValueError("empty file")
            return "exists and readable"
        self.check(path, inspect)

    def get(self, url, json_response=True):
        # Device-local requests must not use a configured public HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=self.timeout) as response:
            content = response.read()
        return json.loads(content) if json_response else content


class Transport(xmlrpc.client.Transport):
    def __init__(self, timeout):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


def run(args):
    checks = Checks(args.timeout)
    os_info = {key: value.strip('"') for key, value in key_values("/etc/os-release").items()}
    detected = TARGETS.get((os_info.get("ID", "").lower(), os_info.get("VERSION_ID"), platform.machine()))
    target = args.target or detected
    if target not in TARGETS.values():
        raise ValueError("unsupported platform; specify --target only for an intended deployment target")
    if target != detected:
        checks.report("target", "FAIL", "requested {}, detected {}".format(target, detected))
    else:
        checks.report("target", "PASS", target)
    if os.geteuid() != 0:
        checks.report("permissions", "WARN", "Run with sudo to read protected RPC/configuration files.")
    device = target.split("-")[0]
    config_dir = "/etc/nav01" if device == "pico" else "/etc/naviai"
    for path in ("/etc/zj_humanoid/device.env", "/etc/zj_humanoid/cyclonedds.xml",
                 "/etc/profile.d/zj_humanoid.sh", config_dir + "/Middleware.env"):
        checks.file(path)

    def identity():
        values = key_values("/etc/zj_humanoid/device.env")
        if not values.get("ROBOT_TYPE") or values.get("ZJ_DEVICE") != device.upper():
            raise ValueError("missing ROBOT_TYPE or mismatched ZJ_DEVICE")
        if args.robot_type and values["ROBOT_TYPE"] != args.robot_type:
            raise ValueError("ROBOT_TYPE does not match --robot-type")
        return values["ROBOT_TYPE"]
    checks.check("device identity", identity)
    checks.file("/opt/ros/{}/setup.bash".format(target.split("-")[1]))

    def release():
        value = json.loads(checks.command(["/usr/local/bin/navi-version", "--json"]))
        current = value["current"]
        if current["target"] != target:
            raise ValueError("release target mismatch")
        if current["robot_type"] != key_values("/etc/zj_humanoid/device.env").get("ROBOT_TYPE"):
            raise ValueError("release robot type mismatch")
        return "release={} build={}".format(current["release"], current["build_id"])
    checks.check("navi-version / successful release", release)
    for name in ("current.json", "status.json"):
        checks.file("/var/lib/naviai/release/" + name)
    checks.report("previous.json", "INFO", "Optional on first successful installation.")
    modules = MODULES.get(target, {})
    if modules:
        agent = "zj-humanoid-{}-supervisor-agent.service".format(device)
        checks.check(agent, lambda: checks.command(["systemctl", "is-active", agent]))
        checks.file(config_dir + "/supervisor-agent/modules.json")
        def password():
            if Path(config_dir + "/supervisor-agent/supervisor-rpc.password").read_text().strip() != "1":
                raise ValueError("RPC password does not match configured fixed password")
            return "configured credential matches (not printed)"
        checks.check("RPC credential", password)
        checks.check("Agent web", lambda: "HTML available" if b"<html" in checks.get(
            args.agent_url + "/", False).lower() else (_ for _ in ()).throw(ValueError("not HTML")))
        def health():
            value = checks.get(args.agent_url + "/api/v1/health")
            if value.get("device") != device:
                raise ValueError("unexpected Agent identity")
            return device
        checks.check("Agent health", health)
        def aggregate():
            result = checks.get(args.agent_url + "/api/v1/modules")["modules"]
            local = {item["name"] for item in result if item.get("device") == device}
            missing = set(modules) - local
            if missing:
                raise ValueError("missing module registrations: " + ", ".join(sorted(missing)))
            for item in result:
                label = "{}/{}".format(item.get("device"), item.get("name"))
                processes = item.get("processes", [])
                healthy = item.get("reachable") and processes and all(p["state"] == "RUNNING" for p in processes)
                checks.report("Agent " + label, "PASS" if healthy else "FAIL",
                              ", ".join(p["name"] + "=" + p["state"] for p in processes) or "unreachable or no processes")
                for process in processes:
                    url = args.agent_url + "/api/v1/modules/{}/processes/{}/log?offset=0&length=128".format(
                        urllib.parse.quote(item["id"], safe=""),
                        urllib.parse.quote(process["name"], safe=""))
                    def log_check(url=url):
                        data = checks.get(url)
                        if "data" not in data:
                            raise ValueError("log response lacks data")
                        return "log API readable (content not printed)"
                    checks.check("Log " + label + "/" + process["name"], log_check)
            return "{} modules checked".format(len(result))
        checks.check("Agent module aggregation", aggregate)
    else:
        checks.report("Supervisor", "INFO", "This target has no unified Agent contract; business checks remain manual.")
    ip = "192.168.217.66" if device == "pico" else "192.168.217.100"
    for name, (service, port, managed) in modules.items():
        checks.check(service, lambda service=service: checks.command(["systemctl", "is-active", service]))
        checks.check(service + " enabled", lambda service=service: checks.command(["systemctl", "is-enabled", service]))
        checks.file(config_dir + "/supervisor-agent/modules.d/" + name + ".json")
        if managed:
            for filename in ("launch.sh", "supervisor-entrypoint.sh"):
                checks.file(config_dir + "/supervised-stack/" + name + "/" + filename)
            checks.file("/run/naviai/" + name + "/supervisord.conf")
        if port is None:
            continue
        def rpc(port=port):
            with xmlrpc.client.ServerProxy("http://agent:1@{}:{}/RPC2".format(ip, port),
                                          transport=Transport(args.timeout)) as proxy:
                processes = proxy.supervisor.getAllProcessInfo()
            if not processes or any(p["statename"] != "RUNNING" for p in processes):
                raise ValueError("empty process list or non-RUNNING processes")
            return "{} processes RUNNING; authentication works".format(len(processes))
        checks.check(name + " direct RPC", rpc)
    if target == "orin-humble":
        for package in ["ros-humble-rmw-cyclonedds-cpp","ros-humble-octomap-msgs","ros-humble-octomap-ros","ros-humble-octomap-server","ros-humble-pcl-ros","ros-humble-rosbag2-storage-mcap","ros-humble-mcap-vendor","liboctomap-dev","libcaca-dev","libcaca0","libslang2-dev","libsdl1.2-dev","libsdl1.2debian","libsdl-image1.2","libsdl-image1.2-dev","zstd","python3-uvicorn","python3-fastapi"]:
            def dependency(package=package):
                state = checks.command(["dpkg-query", "-W", "-f=${Status}", package])
                if state != "install ok installed":
                    raise ValueError(state)
                return state
            checks.check("dependency " + package, dependency)
        def middleware_environment():
            command = (
                "source /etc/naviai/Middleware.env; "
                "for key in ROBOT_TYPE ROBOT_NAME ROS_DOMAIN_ID RMW_IMPLEMENTATION "
                "COMPOSE_PROFILES NAVIGATION_ROBOT_MODEL NAVIGATION_CONFIG_PATH LIDAR_3D_TYPE; do "
                "printf '%s=%s\\n' \"$key\" \"${!key:-}\"; done"
            )
            output = checks.command([
                "env", "-i", "PATH=/usr/sbin:/usr/bin:/sbin:/bin", "bash", "-c", command,
            ])
            values = key_values_from_text(output)
            expected_type = key_values("/etc/zj_humanoid/device.env").get("ROBOT_TYPE")
            if values.get("ROBOT_TYPE") != expected_type:
                raise ValueError("ROBOT_TYPE does not match device.env")
            if values.get("RMW_IMPLEMENTATION") != "rmw_cyclonedds_cpp":
                raise ValueError("RMW_IMPLEMENTATION is not CycloneDDS")
            for key in ("ROS_DOMAIN_ID", "COMPOSE_PROFILES", "NAVIGATION_ROBOT_MODEL", "NAVIGATION_CONFIG_PATH"):
                if not values.get(key):
                    raise ValueError("missing " + key)
            return ", ".join("{}={}".format(key, values.get(key) or "<unset>") for key in values)
        checks.check("Orin middleware environment", middleware_environment)
        checks.file("/etc/naviai/navigation/navigation.env")
        checks.file("/usr/lib/naviai/detect_livox_model.py")
        def config_registry():
            registry = json.loads(Path("/home/naviai/navi_project/schema/version.json").read_text())
            entries = registry["ORIN"]["config"]
            for entry in entries:
                checks.file(entry["template"])
                checks.file(entry["dst"])
            return "{} configuration entries checked".format(len(entries))
        checks.check("configuration registry", config_registry)
        for path in ("/etc/naviai/navi-sensor-host-supervisor.conf", "/etc/naviai/robot/robot_env.sh",
                     "/usr/lib/orin-vision-common-deb/vision_environment.sh"):
            checks.file(path)
    if "vision" in modules:
        checks.check("Vision log access as naviai", lambda: checks.command(
            ["runuser", "-u", "naviai", "--", "test", "-w", "/var/log/naviai/vision/ros"]))
        checks.check("Vision log traversal as naviai", lambda: checks.command(
            ["runuser", "-u", "naviai", "--", "test", "-x", "/var/log/naviai/vision/ros"]))
    checks.report("coverage", "INFO", "Read-only checks only: no service restart, ROS launch or configuration sourcing. "
                  "No business-function, offline-install or file-drift certification.")
    return checks.rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=sorted(TARGETS.values()))
    parser.add_argument("--robot-type")
    parser.add_argument("--agent-url", default="http://127.0.0.1:9080")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        rows = run(args)
    except Exception as error:
        rows = [{"check": "platform/checker", "state": "FAIL", "detail": str(error)}]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print("[{}] {}: {}".format(row["state"], row["check"], row["detail"]))
        print("\nPASS={} FAIL={} WARN={}".format(*(sum(r["state"] == level for r in rows)
                                                  for level in ("PASS", "FAIL", "WARN"))))
    return 1 if any(row["state"] == "FAIL" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
