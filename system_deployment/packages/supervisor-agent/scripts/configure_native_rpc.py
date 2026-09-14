#!/usr/bin/env python3
"""Preserve native Supervisor settings while provisioning authenticated RPC."""
import argparse
import configparser
import os
from pathlib import Path
import re
import shutil
import tempfile


def configure(path, password_file, address):
    secret = Path(password_file).read_text().strip()
    if secret != "1" and not re.fullmatch(r"[0-9a-fA-F]{64}", secret):
        raise ValueError("invalid Supervisor RPC credential")
    path = Path(path)
    original = path.read_text()
    parser = configparser.RawConfigParser(strict=False)
    parser.read_string(original)
    if not parser.has_section("rpcinterface:supervisor"):
        raise ValueError("native configuration lacks Supervisor RPC interface")
    section = "[inet_http_server]\nport={}\nusername=agent\npassword={}\n".format(address, secret)
    pattern = re.compile(r"(?ms)^\[inet_http_server\][^\n]*\n.*?(?=^\[|\Z)")
    updated = pattern.sub(lambda _: section + "\n", original) if pattern.search(original) else original.rstrip() + "\n\n" + section
    if updated == original:
        return
    backup = path.with_name(path.name + ".before-one-stop-rpc")
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    fd, name = tempfile.mkstemp(prefix=".native-rpc-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("password_file")
    parser.add_argument("address")
    args = parser.parse_args()
    configure(args.config, args.password_file, args.address)
