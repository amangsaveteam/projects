#!/usr/bin/env python3
"""Persist release provenance and report the last installation outcome."""
import argparse
import datetime
import json
import os
from pathlib import Path
import tempfile


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o644)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def transition(directory, action, manifest=None, robot_type=None):
    status_path = directory / "status.json"
    if action == "begin":
        attempt = read(manifest)
        if not attempt or attempt.get("schema_version") != 1:
            raise ValueError("invalid release manifest")
        attempt.update(robot_type=robot_type, installation_status="installing", started_at=now())
    else:
        attempt = read(status_path)
        if not attempt or attempt.get("installation_status") != "installing":
            raise ValueError("no installation in progress")
        attempt.update(installation_status="complete" if action == "complete" else "failed",
                       finished_at=now())
        if action == "complete":
            previous = read(directory / "current.json")
            if previous:
                write(directory / "previous.json", previous)
            write(directory / "current.json", attempt)
    write(status_path, attempt)


def show(directory, as_json=False):
    current, status = read(directory / "current.json"), read(directory / "status.json")
    incomplete = bool(status and status.get("installation_status") != "complete")
    if as_json:
        print(json.dumps({"current": current, "last_attempt": status}, ensure_ascii=False, indent=2))
    else:
        if incomplete:
            print("Installation   : {} (system may be partially updated)".format(status["installation_status"]))
            print("Attempted Build: {}".format(status["build_id"]))
        if not current:
            print("No successful release installation recorded.")
        else:
            print("System Release : {}".format(current["release"]))
            print("Build ID       : {}".format(current["build_id"]))
            print("Git Commit     : {}".format(current.get("git_commit") or "unknown"))
            print("Target         : {}".format(current["target"]))
            print("Robot          : {}".format(current["robot_type"]))
            print("Record         : last successful installation; manual changes are not tracked")
            for name, module in current["modules"].items():
                print("{:15}: {} (sha256 {})".format(name, module.get("version") or "unknown",
                                                     module["sha256"]))
    return 2 if incomplete or not current else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/naviai/release"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("action", nargs="?", default="show", choices=("show", "begin", "complete", "fail"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--robot-type")
    args = parser.parse_args()
    if args.action == "show":
        return show(args.state_dir, args.json)
    if args.action == "begin" and (not args.manifest or not args.robot_type):
        parser.error("begin requires --manifest and --robot-type")
    transition(args.state_dir, args.action, args.manifest, args.robot_type)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
