#!/usr/bin/env bash
# Build the target-aware one-stop release defined by package-urls.json.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# urllib does not interpret CIDR entries such as 10.0.0.0/8 in NO_PROXY.
# Keep the configured proxy for public repositories, but always reach the
# internal artifact server directly.
internal_artifact_host="10.51.33.211"
export NO_PROXY="${NO_PROXY:+${NO_PROXY},}${internal_artifact_host}"
export no_proxy="${no_proxy:+${no_proxy},}${internal_artifact_host}"

exec python3 "$script_dir/build_one_stop_package.py" \
    --version "$script_dir/version.json" \
    --urls "$script_dir/package-urls.json" \
    --output-dir "$script_dir/../../dist" \
    "$@"
