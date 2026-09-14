#!/usr/bin/env python3
"""Set the fixed RPC password shared by the device Agent and modules."""

import os
import subprocess
from pathlib import Path


def ensure_secret(path):
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        os.fchmod(output.fileno(), 0o600)
        output.truncate(0)
        output.write("1\n")
    return "1"


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
