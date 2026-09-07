#!/usr/bin/env bash
# Build the target-aware one-stop release defined by package-urls.json.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec python3 "$script_dir/build_one_stop_package.py" \
    --version "$script_dir/version.json" \
    --urls "$script_dir/package-urls.json" \
    --output-dir "$script_dir/../dist/one-stop" \
    "$@"
