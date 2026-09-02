#!/usr/bin/env python3
"""Build a global common dependency bundle owned by system_deployment.

Module-level common_dep packages deliberately stay outside this entry point.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


COMMON_DIR = Path(__file__).resolve().parent
DEPLOYMENT_ROOT = COMMON_DIR.parent
GLOBAL_CONFIGS = frozenset({"orin-common", "orin-common-humble", "pico-common", "pico-jazzy-common", "rdk-common"})


def selected_config(arguments: List[str]) -> Optional[str]:
    for index, argument in enumerate(arguments):
        if argument == "--config":
            return arguments[index + 1] if index + 1 < len(arguments) else None
        if argument.startswith("--config="):
            return argument.partition("=")[2]
    return None


def main() -> int:
    config = selected_config(sys.argv[1:])
    if config not in GLOBAL_CONFIGS:
        supported = ", ".join(sorted(GLOBAL_CONFIGS))
        print(
            f"ERROR: system_deployment owns only global common configs ({supported}); "
            "module common_dep configs must use scripts/build/build_dependency_deb.py.",
            file=sys.stderr,
        )
        return 2
    legacy_builder = DEPLOYMENT_ROOT.parent / "scripts/build/build_dependency_deb.py"
    if legacy_builder.is_file():
        command = [sys.executable, str(legacy_builder), *sys.argv[1:]]
    else:
        config_path = COMMON_DIR / "configs" / f"{config}.json"
        command = [sys.executable, str(COMMON_DIR / "build_offline_common_bundle.py"), "--config", str(config_path)]
        print("INFO: legacy builder is unavailable; using the local offline common carrier builder.")
    environment = os.environ.copy()
    environment["NAVI_SYSTEM_DEPLOYMENT_COMMON_BUILDER"] = "1"
    return subprocess.run(command, env=environment, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
