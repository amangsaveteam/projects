#!/usr/bin/env python3
"""Set the fixed Orin Agent and module RPC password."""

import os
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


if __name__ == "__main__":
    ensure_secret("/etc/naviai/supervisor-agent/supervisor-rpc.password")
