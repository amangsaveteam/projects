#!/usr/bin/env python3
"""Append the managed XML-RPC endpoint to the vendor Sensor supervisor config."""

import argparse
import re
from pathlib import Path


BEGIN = "# BEGIN navi-orin-stack supervisor RPC"
END = "# END navi-orin-stack supervisor RPC"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    args = parser.parse_args()
    password = args.password_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", password):
        raise SystemExit("invalid Supervisor RPC credential: {}".format(args.password_file))
    text = args.config.read_text(encoding="utf-8")
    text = re.sub(
        r"\n?{}.*?{}\n?".format(re.escape(BEGIN), re.escape(END)),
        "\n", text, flags=re.DOTALL,
    ).rstrip() + "\n"
    text += "\n{}\n[inet_http_server]\nport=192.168.217.100:19001\nusername=agent\npassword={}\n{}\n".format(BEGIN, password, END)
    args.config.write_text(text, encoding="utf-8")
    args.config.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
