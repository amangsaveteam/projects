#!/usr/bin/env python3
"""Add a Supervisor Agent XML-RPC listener to a vendor Sensor host config."""

import argparse
import re
from pathlib import Path


BEGIN = "# BEGIN navi-one-stop sensor supervisor RPC"
END = "# END navi-one-stop sensor supervisor RPC"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    password = args.password_file.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{64}", password):
        raise SystemExit("invalid Supervisor RPC credential: {}".format(args.password_file))
    text = args.config.read_text(encoding="utf-8")
    text = re.sub(r"\n?{}.*?{}\n?".format(re.escape(BEGIN), re.escape(END)), "\n", text, flags=re.DOTALL).rstrip() + "\n"
    text += "\n{}\n[inet_http_server]\nport={}:{}\nusername=agent\npassword={}\n{}\n".format(
        BEGIN, args.host, args.port, password, END
    )
    args.config.write_text(text, encoding="utf-8")
    args.config.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
