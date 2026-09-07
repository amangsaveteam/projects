#!/usr/bin/env python3
"""Compatibility entry point for the Orin Humble supervised-stack manifest."""

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from supervised_stack.build_supervised_stack import StackError, build as build_stack  # noqa: E402

ARTIFACTS = {
    "sensor": "navi_sensor_installer-2.0.0-release-humble-arm64.run",
    "robot": "navi_robot_installer-2.0.0-release-humble-arm64.run",
    "audio": "navi_audio_installer-2.0.0-release-humble-arm64.run",
}
GENERATED = {
    "chassis": ROOT / "output/navi_chassis_orin_installer-2.0.0-release-humble-arm64.run",
    "agent": ROOT / "output/navi_orin_supervisor_agent-1.0.0-arm64.run",
}
MANIFEST = ROOT / "supervised_stack/configs/orin-humble.json"


def build(artifacts_dir: Path, output_dir: Path) -> Path:
    """Build the historical package name via the generic stack builder."""
    overrides = {name: artifacts_dir / filename for name, filename in ARTIFACTS.items()}
    overrides.update(GENERATED)
    return build_stack(MANIFEST, output_dir, overrides)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=ROOT.parent / "dist")
    parser.add_argument("--output-dir", type=Path, default=ROOT.parent / "dist")
    args = parser.parse_args()
    try:
        output = build(args.artifacts_dir.resolve(), args.output_dir.resolve())
    except StackError as error:
        parser.error(str(error))
    print("Built {}".format(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
