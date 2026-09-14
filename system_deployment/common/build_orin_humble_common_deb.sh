#!/usr/bin/env bash
# Build the Orin Ubuntu 22.04 / ROS 2 Humble Common carrier on its arm64 builder.
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_directory/build_common.py" --config orin-common-humble
