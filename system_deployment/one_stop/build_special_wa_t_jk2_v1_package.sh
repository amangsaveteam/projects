#!/usr/bin/env bash
# Build the WA-T / JK2-V1 Orin and Pico one-stop installer.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work_dir="$(mktemp -d "${TMPDIR:-/tmp}/navi-wa-t-jk2-v1-package.XXXXXX")"
trap 'rm -rf "$work_dir"' EXIT

python3 - "$script_dir" "$work_dir" "$@" <<'PY'
import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

script_dir = Path(sys.argv[1])
work_dir = Path(sys.argv[2])
dry_run = "--dry-run" in sys.argv[3:]
# Debian considers "~humble" lower than the plain 2.0.0 required by
# existing Orin module packages.  Use a normal Debian revision instead.
version = "2.0.0-24"


def build_empty_deb(package: str, depends: str = "") -> Path:
    staging = work_dir / package
    control = staging / "DEBIAN"
    control.mkdir(parents=True)
    fields = [
        f"Package: {package}",
        f"Version: {version}",
        "Section: misc",
        "Priority: optional",
        "Architecture: all",
        "Maintainer: NaviAI <release@naviai.local>",
        "Description: WA-T/JK2-V1 compatibility marker package",
    ]
    if depends:
        fields.insert(2, f"Depends: {depends}")
    (control / "control").write_text("\n".join(fields) + "\n", encoding="utf-8")
    output = work_dir / f"{package}_{version}_all.deb"
    subprocess.run(["dpkg-deb", "--build", str(staging), str(output)], check=True, stdout=subprocess.DEVNULL)
    return output

navi_common = build_empty_deb("navi-common-dep")
orin_common = build_empty_deb("orin-common-deb", f"navi-common-dep (= {version})")

with (script_dir / "special-wa-t-jk2-v1-package-urls.json").open(encoding="utf-8") as stream:
    urls = json.load(stream)

orin = urls["targets"]["orin-humble"]
orbbec = Path(os.environ.get("ORBBEC_OFFLINE_DEB", str(
    script_dir.parents[1] / "dist/common/orin/sensor/navi_orbbec_dep-2.9.3-humble-arm64.deb"
))).expanduser().resolve()
if not orbbec.is_file() and not dry_run:
    if os.environ.get("ORBBEC_OFFLINE_DEB"):
        raise SystemExit("ORBBEC_OFFLINE_DEB does not exist: " + str(orbbec))
    print("Building Orbbec ARM64 offline dependencies for the one-stop installer", flush=True)
    subprocess.run([
        sys.executable, str(script_dir.parent / "common/build_offline_common_bundle.py"),
        "--config", str(script_dir.parent / "common/configs/orin-orbbec-humble.json"),
    ], check=True)

digest = hashlib.sha256()
if orbbec.is_file():
    for field, expected in (("Package", "navi-orbbec-dep"), ("Architecture", "arm64")):
        actual = subprocess.check_output(["dpkg-deb", "-f", str(orbbec), field], text=True).strip()
        if actual != expected:
            raise SystemExit("Invalid Orbbec offline bundle " + field + ": " + actual)
    with orbbec.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
# Place after the sensor/common dependency carriers, before module .run files.
orin["extra_debs"].append({
    "name": "orbbec-camera", "url": orbbec.as_uri(),
    "sha256": digest.hexdigest() if orbbec.is_file() else "", "installers": ["/usr/sbin/install_orbbec_deps.sh"],
    "robot_types": ["WA-T"],
})
orin["extra_debs"] = [
    {"name": "navi-common-marker", "url": navi_common.resolve().as_uri(), "sha256": "", "installers": []},
    {"name": "orin-common-marker", "url": orin_common.resolve().as_uri(), "sha256": "", "installers": []},
    *orin["extra_debs"],
]
(work_dir / "package-urls.json").write_text(json.dumps(urls, indent=2) + "\n", encoding="utf-8")
PY

python3 "$script_dir/build_one_stop_package.py" \
    --version "$script_dir/special-wa-t-jk2-v1-version.json" \
    --urls "$work_dir/package-urls.json" \
    --supervisor "$script_dir/special-wa-t-jk2-v1-supervisor.json" \
    --output-dir "$script_dir/../../dist" \
    "$@"
