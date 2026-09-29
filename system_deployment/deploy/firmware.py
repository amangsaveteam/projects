"""Deployment boundary for firmware artifacts.

This module is the stable home for deployment orchestration. Target-specific
installers can be moved here incrementally without changing the build API.
"""
from pathlib import Path


def validate_firmware_root(root: Path) -> Path:
    """Validate the unpacked firmware layout before deployment."""
    root = Path(root).resolve()
    required = ("version.json",)
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ValueError(f"invalid firmware root {root}: missing {', '.join(missing)}")
    return root
