#!/usr/bin/env python3
"""Create persistent Orin Agent secrets without overwriting provisioned peers."""

import os
import secrets
import subprocess
from pathlib import Path


def ensure_secret(path):
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        os.chmod(str(target), 0o600)
        return
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(secrets.token_hex(32) + "\n")


if __name__ == "__main__":
    ensure_secret("/etc/naviai/supervisor-agent/supervisor-rpc.password")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    chassis_service = "navi-orin-chassis.service"
    if subprocess.run(["systemctl", "cat", chassis_service], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode == 0:
        subprocess.run(["systemctl", "restart", chassis_service], check=True)
