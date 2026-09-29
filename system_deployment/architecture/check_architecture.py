#!/usr/bin/env python3
"""Fast repository architecture guard used before deployment changes."""
from pathlib import Path
import subprocess
import sys

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
print("ARCHITECTURE CHECK PASSED")
