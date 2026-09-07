#!/usr/bin/env python3
"""Create persistent secrets shared by the device Agent and module RPCs."""

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


def main():
    for path in (
        "/etc/nav01/supervisor-agent/supervisor-rpc.password",
    ):
        ensure_secret(path)
    service = "navi-pico-upperlimb.service"
    if subprocess.run(["systemctl", "is-active", "--quiet", service], check=False).returncode == 0:
        subprocess.run(["systemctl", "restart", service], check=True)


if __name__ == "__main__":
    main()
