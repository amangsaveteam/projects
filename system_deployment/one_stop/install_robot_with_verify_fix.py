#!/usr/bin/env python3
"""Correct the known Orin Robot verifier status leak before installation."""

import argparse
import re
import subprocess
import tempfile
from pathlib import Path


VERIFIER = re.compile(r"(?ms)^verify_robot_runtime\(\) \{\n.*?^\}")
LEGACY_TAIL = '''  for legacy in ${LEGACY_ROBOT_SERVICES}; do
    systemctl is-active --quiet "${legacy}" && die "Legacy service is still active: ${legacy}"
  done
}'''


def fix_robot_verifier(script: str) -> str:
    matches = list(VERIFIER.finditer(script))
    if len(matches) != 1:
        raise RuntimeError("cannot identify the Orin Robot runtime verifier")
    match = matches[0]
    body = match.group()
    fixed_tail = LEGACY_TAIL[:-1] + "  return 0\n}"
    if body.endswith(fixed_tail):
        return script
    if not body.endswith(LEGACY_TAIL):
        raise RuntimeError("unrecognized Orin Robot verifier; refusing to modify it")
    fixed = body[:-len(LEGACY_TAIL)] + fixed_tail
    return script[:match.start()] + fixed + script[match.end():]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="navi-robot-install-") as workspace:
        payload = Path(workspace) / "payload"
        result = subprocess.run([
            "/bin/bash", str(args.run.resolve()), "--noexec", "--target", str(payload)
        ])
        if result.returncode:
            return result.returncode
        installer = payload / "install.sh"
        installer.write_text(fix_robot_verifier(installer.read_text(encoding="utf-8")), encoding="utf-8")
        arguments = args.arguments
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        return subprocess.run(["/bin/bash", str(installer), *arguments], cwd=payload).returncode


if __name__ == "__main__":
    raise SystemExit(main())
