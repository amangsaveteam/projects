#!/usr/bin/env python3
"""Fast repository architecture guard used before deployment changes."""
from pathlib import Path
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
errors = []

if (ROOT / "one_stop").exists():
    errors.append("legacy one_stop directory exists")
for name in ("version.json", "package-urls.json", "supervisor.json"):
    matches = list(ROOT.rglob(name))
    matches = [p for p in matches if ".git" not in p.parts and "assets" not in p.parts]
    expected = ROOT / "release" / name
    if matches != [expected]:
        errors.append(f"{name} must have one source at release/{name}: {matches}")

for path in ROOT.rglob("*"):
    if any(part in {".git", "__pycache__"} for part in path.parts):
        continue
    if path.is_file() and path.suffix in {".deb", ".ddeb", ".run", ".firmware", ".pyc"}:
        errors.append(f"generated artifact in source tree: {path.relative_to(ROOT)}")

try:
    tracked = subprocess.check_output(["git", "-C", str(ROOT.parent), "ls-files"], text=True)
    for item in tracked.splitlines():
        if any(item.endswith(ext) for ext in (".deb", ".ddeb", ".run", ".firmware", ".pyc")) and (ROOT.parent / item).exists():
            errors.append(f"generated artifact tracked by git: {item}")
except (OSError, subprocess.CalledProcessError) as exc:
    errors.append(f"git scan failed: {exc}")

if errors:
    print("ARCHITECTURE CHECK FAILED")
    print("\n".join(f"- {item}" for item in errors))
    sys.exit(1)

# Validate the release contracts that cannot be enforced by path checks alone.
try:
    urls = json.loads((ROOT / "release/package-urls.json").read_text())
    supervisor = json.loads((ROOT / "release/supervisor.json").read_text())
    targets = urls.get("targets", {})
    supervisor_targets = supervisor.get("targets", {})
    if not set(supervisor_targets).issubset(set(targets)):
        errors.append("supervisor.json contains targets missing from package-urls.json")
    for target_id, target in supervisor_targets.items():
        modules = target.get("supervisor_modules", [])
        ids = [m.get("id") for m in modules]
        ports = [m.get("port") for m in modules if m.get("port") is not None]
        if None in ids or len(ids) != len(set(ids)):
            errors.append(f"{target_id}: Supervisor module ids must be unique and non-empty")
        if len(ports) != len(set(ports)):
            errors.append(f"{target_id}: Supervisor module ports must be unique")
        for index, module in enumerate(modules):
            mode = module.get("mode")
            if mode not in {"managed", "external", "systemd"}:
                errors.append(f"{target_id}.supervisor_modules[{index}]: unsupported mode {mode!r}")
            if mode == "managed" and not module.get("command"):
                errors.append(f"{target_id}.supervisor_modules[{index}]: managed module needs command")
    for target_id, target in targets.items():
        for field in ("extra_debs", "runs"):
            names = [item.get("name") for item in target.get(field, []) if isinstance(item, dict)]
            if len(names) != len(set(names)):
                errors.append(f"{target_id}.{field}: package names must be unique")
except (OSError, json.JSONDecodeError, AttributeError) as exc:
    errors.append(f"release contract scan failed: {exc}")

if errors:
    print("ARCHITECTURE CHECK FAILED")
    print("\n".join(f"- {item}" for item in errors))
    sys.exit(1)
print("ARCHITECTURE CHECK PASSED")
