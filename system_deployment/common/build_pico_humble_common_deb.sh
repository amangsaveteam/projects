#!/usr/bin/env bash
# Build the Pico Ubuntu 20.04 / ROS 2 Humble Common carrier on its amd64 builder.
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_directory/build_common.py" --config pico-common
