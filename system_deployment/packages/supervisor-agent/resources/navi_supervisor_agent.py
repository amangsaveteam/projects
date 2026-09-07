#!/usr/bin/env python3
"""Device-level HTTP gateway for module Supervisors and peer Agents."""

import argparse
import http.client
import json
import os
import secrets
import stat
import sys
import urllib.parse
import urllib.error
import urllib.request
import xmlrpc.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = 3
MAX_LOG_LENGTH = 32 * 1024


def read_secret(path):
    value = Path(path).read_text(encoding="utf-8").strip()
    if len(value) != 64 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("secret at {} must contain exactly 64 hexadecimal characters".format(path))
    return value


def ensure_secret(path):
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(str(target), stat.S_IRUSR | stat.S_IWUSR)
        return read_secret(target)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        value = secrets.token_hex(32)
        output.write(value + "\n")
    return value


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout_seconds):
        super().__init__()
        self.timeout_seconds = timeout_seconds

    def make_connection(self, host):
        # Keep Transport's userinfo handling: ServerProxy passes
        # ``agent:password@host:port`` here, which must be split into a Basic
        # Authorization header before HTTPConnection receives the hostname.
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        connection_host, self._extra_headers, _ = self.get_host_info(host)
        self._connection = host, http.client.HTTPConnection(connection_host, timeout=self.timeout_seconds)
        return self._connection[1]


class Agent:
    def __init__(self, config):
        self.config = config
        self.rpc_username = config.get("rpc_username", "agent")
        self.rpc_password = read_secret(config["rpc_password_file"])
        self.timeout_seconds = int(config.get("rpc_timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self.modules = config.get("modules", {})
        self.remote_agents = config.get("remote_agents", {})

    def proxy(self, module):
        specification = self.modules[module]
        endpoint = urllib.parse.urlsplit(specification["endpoint"])
        if endpoint.scheme != "http" or not endpoint.hostname or not endpoint.port:
            raise ValueError("module {} has an invalid HTTP endpoint".format(module))
        credentials = "{}:{}".format(
            urllib.parse.quote(self.rpc_username, safe=""),
            urllib.parse.quote(self.rpc_password, safe=""),
        )
        url = "http://{}@{}:{}{}".format(credentials, endpoint.hostname, endpoint.port, endpoint.path or "/RPC2")
        return xmlrpc.client.ServerProxy(url, allow_none=True, transport=TimeoutTransport(self.timeout_seconds))

    def module_status(self, module):
        try:
            processes = self.proxy(module).supervisor.getAllProcessInfo()
        except (OSError, ValueError, xmlrpc.client.Error) as error:
            return {"name": module, "reachable": False, "error": str(error), "processes": []}
        return {
            "name": module,
            "reachable": True,
            "processes": [
                {
                    "name": process["name"],
                    "state": process["statename"],
                    "pid": process["pid"],
                    "description": process["description"],
                    "exit_status": process["exitstatus"],
                    "spawn_error": process["spawnerr"],
                }
                for process in processes
            ],
        }

    def all_status(self):
        results = []
        for name in sorted(self.modules):
            result = self.module_status(name)
            result["id"] = name
            result["device"] = self.config.get("device", "local")
            results.append(result)
        for agent_name in sorted(self.remote_agents):
            specification = self.remote_agents[agent_name]
            try:
                response = self.remote_request(specification, "GET", "/api/v1/modules")
                remote_device = response.get("device", agent_name)
                remote_modules = response.get("modules", [])
                if not isinstance(remote_modules, list):
                    raise ValueError("remote Agent returned an invalid modules response")
                for result in remote_modules:
                    if not isinstance(result, dict) or not isinstance(result.get("name"), str):
                        raise ValueError("remote Agent returned an invalid module entry")
                    result = dict(result)
                    result["id"] = "{}/{}".format(agent_name, result.get("id", result["name"]))
                    result["device"] = remote_device
                    results.append(result)
            except (OSError, ValueError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as error:
                results.append({
                    "id": agent_name,
                    "name": agent_name,
                    "device": agent_name,
                    "reachable": False,
                    "error": "remote Agent unavailable: {}".format(error),
                    "processes": [],
                })
        return results

    def remote_request(self, specification, method, path):
        endpoint = specification.get("endpoint", "").rstrip("/")
        request = urllib.request.Request(
            endpoint + path,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def split_module(self, module):
        if "/" not in module:
            if module not in self.modules:
                raise KeyError("unknown module: {}".format(module))
            return None, module
        agent_name, remote_module = module.split("/", 1)
        if agent_name not in self.remote_agents or not remote_module:
            raise KeyError("unknown module: {}".format(module))
        return agent_name, remote_module

    def process_action(self, module, process, action):
        agent_name, target_module = self.split_module(module)
        if agent_name is not None:
            return self.remote_request(
                self.remote_agents[agent_name],
                "POST",
                "/api/v1/modules/{}/processes/{}/{}".format(
                    urllib.parse.quote(target_module, safe=""),
                    urllib.parse.quote(process, safe=""),
                    urllib.parse.quote(action, safe=""),
                ),
            )
        proxy = self.proxy(target_module).supervisor
        if action == "start":
            changed = proxy.startProcess(process, True)
        elif action == "stop":
            changed = proxy.stopProcess(process, True)
        elif action == "restart":
            try:
                proxy.stopProcess(process, True)
            except xmlrpc.client.Fault:
                pass
            changed = proxy.startProcess(process, True)
        else:
            raise ValueError("unsupported action")
        return {"changed": bool(changed), "module": target_module, "process": process, "action": action}

    def process_log(self, module, process, offset, length):
        agent_name, target_module = self.split_module(module)
        if agent_name is not None:
            return self.remote_request(
                self.remote_agents[agent_name],
                "GET",
                "/api/v1/modules/{}/processes/{}/log?offset={}&length={}".format(
                    urllib.parse.quote(target_module, safe=""),
                    urllib.parse.quote(process, safe=""),
                    offset,
                    length,
                ),
            )
        data, next_offset, overflow = self.proxy(target_module).supervisor.tailProcessStdoutLog(process, offset, length)
        return {"module": target_module, "process": process, "data": data, "next_offset": next_offset, "overflow": overflow}


INDEX_HTML = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>Navi Supervisor Agent</title>
<style>
body{font:16px system-ui;margin:2rem;max-width:1100px}button{padding:.45rem;margin:.15rem}
article{border:1px solid #ddd;padding:1rem;margin:1rem 0;border-radius:.4rem}.RUNNING{color:#087f23}.FATAL,.BACKOFF,.EXITED{color:#b3261e}
.process{border-top:1px solid #eee;padding:.7rem 0}.meta{color:#555;font-size:.9rem}.logs{width:100%;max-height:22rem;overflow:auto;background:#111;color:#eee;padding:.7rem;white-space:pre-wrap;box-sizing:border-box}
</style>
<h1>Navi Supervisor Agent</h1><button onclick="load()">立即刷新</button><label><input id="auto" type="checkbox" checked> 自动刷新（3 秒）</label><p id="message"></p><main id="modules"></main>
<script>
const message=document.querySelector('#message'),modules=document.querySelector('#modules'),auto=document.querySelector('#auto');
async function request(path,init={}){const r=await fetch(path,init);if(!r.ok)throw Error(await r.text());return r.json()}
function processView(module,process){const exit=process.exit_status===undefined?'—':process.exit_status;const error=process.spawn_error?' / '+process.spawn_error:'';return '<section class="process"><p><b>'+process.name+'</b> <span class="'+process.state+'">'+process.state+'</span></p><p class="meta">PID: '+process.pid+' · 退出码: '+exit+error+'</p><button data-module="'+module.id+'" data-process="'+process.name+'" data-action="start">启动</button><button data-module="'+module.id+'" data-process="'+process.name+'" data-action="stop">停止</button><button data-module="'+module.id+'" data-process="'+process.name+'" data-action="restart">重启</button><button data-module="'+module.id+'" data-process="'+process.name+'" data-action="log">日志尾部</button><pre id="log-'+module.id.replace(/[^a-z0-9]/gi,'_')+'-'+process.name.replace(/[^a-z0-9]/gi,'_')+'" class="logs" hidden></pre></section>'}
async function load(){try{const data=await request('/api/v1/modules');modules.replaceChildren(...data.modules.map(m=>{const a=document.createElement('article');a.innerHTML='<h2>'+m.device+' / '+m.name+'</h2>'+(!m.reachable?'<p>'+m.error+'</p>':m.processes.map(p=>processView(m,p)).join(''));return a}));message.textContent='已更新：'+new Date().toLocaleTimeString()}catch(e){message.textContent=e.message}}
modules.onclick=async event=>{const button=event.target;if(!button.dataset.action)return;const module=button.dataset.module,process=button.dataset.process,action=button.dataset.action;try{if(action==='log'){const data=await request('/api/v1/modules/'+encodeURIComponent(module)+'/processes/'+encodeURIComponent(process)+'/log?offset=0&length=32768');const output=document.querySelector('#log-'+module.replace(/[^a-z0-9]/gi,'_')+'-'+process.replace(/[^a-z0-9]/gi,'_'));output.textContent=data.data;output.hidden=false;output.scrollTop=output.scrollHeight;return}await request('/api/v1/modules/'+encodeURIComponent(module)+'/processes/'+encodeURIComponent(process)+'/'+action,{method:'POST'});load()}catch(e){message.textContent=e.message}};
setInterval(()=>{if(auto.checked)load()},3000);load();
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    agent = None

    def log_message(self, format_string, *arguments):
        sys.stderr.write("supervisor-agent: " + format_string % arguments + "\n")

    def write_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/":
            content = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if parsed.path == "/api/v1/health":
            self.write_json(200, {"ok": True, "device": self.agent.config.get("device", "unknown")})
            return
        if parsed.path == "/api/v1/modules":
            self.write_json(200, {"device": self.agent.config.get("device", "unknown"), "modules": self.agent.all_status()})
            return
        parts = parsed.path.split("/")
        if len(parts) == 8 and parts[:4] == ["", "api", "v1", "modules"] and parts[5] == "processes" and parts[7] == "log":
            try:
                query = urllib.parse.parse_qs(parsed.query)
                offset = max(0, int(query.get("offset", ["0"])[0]))
                length = min(MAX_LOG_LENGTH, max(1, int(query.get("length", [str(MAX_LOG_LENGTH)])[0])))
                self.write_json(200, self.agent.process_log(urllib.parse.unquote(parts[4]), urllib.parse.unquote(parts[6]), offset, length))
            except (ValueError, OSError, xmlrpc.client.Error) as error:
                self.write_json(502, {"error": str(error)})
            return
        self.write_json(404, {"error": "not found"})

    def do_POST(self):
        parts = urllib.parse.urlsplit(self.path).path.split("/")
        if len(parts) != 8 or parts[:4] != ["", "api", "v1", "modules"] or parts[5] != "processes":
            self.write_json(404, {"error": "not found"})
            return
        try:
            self.write_json(200, self.agent.process_action(urllib.parse.unquote(parts[4]), urllib.parse.unquote(parts[6]), parts[7]))
        except (KeyError, ValueError, OSError, xmlrpc.client.Error) as error:
            self.write_json(502, {"error": str(error)})


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    directory = config.get("module_config_directory", "")
    if directory:
        for overlay_path in sorted(Path(directory).glob("*.json")):
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            for field in ("modules", "remote_agents"):
                values = overlay.get(field, {})
                if not isinstance(values, dict):
                    raise ValueError("{} in {} must be an object".format(field, overlay_path))
                config.setdefault(field, {}).update(values)
    if not isinstance(config.get("modules"), dict) or not isinstance(config.get("remote_agents", {}), dict) or not config.get("rpc_password_file"):
        raise ValueError("config must define modules, remote_agents and rpc_password_file")
    return config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    Handler.agent = Agent(config)
    server = ThreadingHTTPServer((config.get("listen", "0.0.0.0"), int(config.get("port", 9080))), Handler)
    print("Navi Supervisor Agent listening on {}:{}".format(*server.server_address), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
