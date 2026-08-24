#!/usr/bin/env python3
"""Deploy a declared set of system files atomically and optionally activate systemd units.

The manifest is intentionally owned by system_deployment.  Module delivery
installers should keep owning their own files and invoke this tool only for
files that are truly shared by the whole system.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from deployment_core import DeploymentAction, DeploymentPhase, DeploymentRunner


class DeployError(RuntimeError):
    pass


PROJECT_ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="JSON manifest, relative to this project or absolute")
    parser.add_argument("--root", default="/", help="deployment root; use a temporary directory for staging tests")
    parser.add_argument("--dry-run", action="store_true", help="validate and print planned changes without writing")
    return parser.parse_args()


def resolve_manifest(value: str) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError("cannot read system deployment manifest {}: {}".format(path, exc)) from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise DeployError("manifest schema_version must be 1")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise DeployError("manifest.files must contain at least one file")
    if set(data) - {"schema_version", "files", "systemd"}:
        raise DeployError("manifest has unsupported keys")
    return data


def destination(root: Path, target: str) -> Path:
    path = Path(target)
    if not path.is_absolute() or ".." in path.parts:
        raise DeployError("target must be an absolute, traversal-free path: {}".format(target))
    root = root.resolve()
    candidate = root.joinpath(*path.parts[1:])
    resolved_parent = candidate.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise DeployError("target escapes deployment root: {}".format(target))
    return candidate


def file_specs(manifest: Dict[str, Any], manifest_path: Path, root: Path) -> List[Tuple[Path, Path, int]]:
    specs = []
    targets = set()
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) - {"source", "target", "mode"}:
            raise DeployError("each file entry supports only source, target and mode")
        if not isinstance(item.get("source"), str) or not isinstance(item.get("target"), str):
            raise DeployError("each file entry requires string source and target")
        source = (manifest_path.parent / item["source"]).resolve()
        if not source.is_file() or PROJECT_ROOT not in source.parents:
            raise DeployError("source must be a project file: {}".format(item["source"]))
        target = destination(root, item["target"])
        if target in targets:
            raise DeployError("duplicate target: {}".format(item["target"]))
        targets.add(target)
        try:
            mode = int(str(item.get("mode", "0644")), 8)
        except ValueError as exc:
            raise DeployError("invalid file mode for {}".format(item["target"])) from exc
        specs.append((source, target, mode))
    return specs


def systemd_actions(manifest: Dict[str, Any], root: Path) -> Dict[str, List[str]]:
    data = manifest.get("systemd", {})
    if not isinstance(data, dict) or set(data) - {"enable", "restart"}:
        raise DeployError("systemd supports only enable and restart lists")
    if root != Path("/") and data:
        raise DeployError("systemd activation is only allowed with --root /")
    actions = {key: data.get(key, []) for key in ("enable", "restart")}
    for key, services in actions.items():
        if not isinstance(services, list) or any(not isinstance(service, str) or not service.endswith(".service") for service in services):
            raise DeployError("systemd.{} must be a list of .service names".format(key))
    return actions


def main() -> int:
    args = parse_args()
    backups: List[Tuple[Path, Optional[Path]]] = []
    try:
        manifest_path = resolve_manifest(args.manifest)
        root = Path(args.root).resolve()
        manifest = load_manifest(manifest_path)
        specs = file_specs(manifest, manifest_path, root)
        actions = systemd_actions(manifest, root)
        if root == Path("/") and os.geteuid() != 0 and not args.dry_run:
            raise DeployError("deploying to / requires root privileges")

        def preflight() -> None:
            for source, target, _mode in specs:
                print("{} -> {}".format(source.relative_to(PROJECT_ROOT), target))

        def stage() -> None:
            if args.dry_run:
                return
            for source, target, mode in specs:
                target.parent.mkdir(parents=True, exist_ok=True)
                backup = None
                if target.exists() or target.is_symlink():
                    descriptor, backup_name = tempfile.mkstemp(prefix=".navi-system-deployment-", dir=str(target.parent))
                    os.close(descriptor)
                    backup = Path(backup_name)
                    shutil.copy2(target, backup, follow_symlinks=False)
                descriptor, staged_name = tempfile.mkstemp(prefix=".navi-system-deployment-", dir=str(target.parent))
                os.close(descriptor)
                staged = Path(staged_name)
                shutil.copy2(source, staged)
                staged.chmod(mode)
                os.replace(staged, target)
                backups.append((target, backup))

        def start() -> None:
            if args.dry_run or root != Path("/"):
                return
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            for service in actions["enable"]:
                subprocess.run(["systemctl", "enable", service], check=True)
            for service in actions["restart"]:
                subprocess.run(["systemctl", "restart", service], check=True)

        def rollback(_error: BaseException, _completed: Tuple[DeploymentPhase, ...]) -> None:
            for target, backup in reversed(backups):
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)

        result = DeploymentRunner(
            [
                DeploymentAction(DeploymentPhase.PREFLIGHT, preflight),
                DeploymentAction(DeploymentPhase.STAGE, stage),
                DeploymentAction(DeploymentPhase.START, start),
            ],
            rollback=rollback,
        ).run()
        if not result.ok:
            raise result.error or DeployError("system deployment failed")
        for _target, backup in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)
        print("PASS: deployed {} system file(s)".format(len(specs)))
        return 0
    except (DeployError, OSError, subprocess.CalledProcessError) as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
