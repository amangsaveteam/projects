#!/usr/bin/env bash
# One entry point for building the supervised Pico, Orin, or unified release.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment_root="$(cd "$script_dir/.." && pwd)"
output_dir="$deployment_root/dist"
target="${1:-unified}"

usage() {
    cat <<'EOF'
Usage: build_humble_release.sh [pico|orin|unified] [--output-dir DIRECTORY]

Update or add modules in the corresponding configs/*.json manifest, then run
this command again. `unified` rebuilds both platform installers and embeds
them in one auto-detecting run package.
EOF
}

shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) shift; output_dir="${1:?--output-dir needs a value}" ;;
        --output-dir=*) output_dir="${1#--output-dir=}" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

case "$target" in
    pico)
        exec python3 "$script_dir/build_supervised_stack.py" \
            --manifest "$script_dir/configs/pico-humble.json" --output-dir "$output_dir"
        ;;
    orin)
        exec python3 "$script_dir/build_supervised_stack.py" \
            --manifest "$script_dir/configs/orin-humble.json" --output-dir "$output_dir"
        ;;
    unified)
        exec python3 "$script_dir/build_unified_stack.py" \
            --manifest "$script_dir/configs/unified-humble.json" --output-dir "$output_dir"
        ;;
    *)
        echo "ERROR: build target must be pico, orin, or unified" >&2
        usage >&2
        exit 2
        ;;
esac
