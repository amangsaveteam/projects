#!/usr/bin/env python3
"""Canonical Python entry point for firmware construction.

The legacy implementation is invoked through the compatibility module while
configuration and future build stages live under ``system_deployment/build``.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "one_stop" / "build_one_stop_package.py"
namespace = {"__name__": "__main__", "__file__": str(LEGACY)}
exec(compile(LEGACY.read_text(encoding="utf-8"), str(LEGACY), "exec"), namespace)
