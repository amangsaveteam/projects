#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment_root="$(cd "${script_dir}/../.." && pwd)"

exec python3 "${deployment_root}/build_run_package.py" \
    --manifest "${script_dir}/upperlimb-pico-run.manifest.json" \
    --output-dir "${deployment_root}/output"
