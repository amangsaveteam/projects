#!/usr/bin/env bash
# Read-only preflight check for building the Orin ROS 2 Jazzy common bundle.
# It never runs apt update/install and is safe to execute repeatedly.

set -u -o pipefail

readonly TARGET_OS_ID="ubuntu"
readonly TARGET_OS_VERSION="24.04"
readonly TARGET_ARCH="arm64"
readonly ARTIFACT="navi_common_dep-2.0.0-release-jazzy-arm64.deb"

installed_count=0
missing_count=0
outdated_count=0
unavailable_count=0
warning_count=0

usage() {
  cat <<'EOF'
Usage: check_orin_jazzy_arm64_env.sh [--strict-target]

Checks the local machine for the dependency currently carried by the Orin ROS 2
Jazzy common package.  This script only reads local system/APT state; it does
not run apt update, apt install, sudo, or make any other system change.

Options:
  --strict-target  Return failure when the host is not Ubuntu 24.04 arm64 Orin.
  -h, --help       Show this help.
EOF
}

strict_target=false
while (($#)); do
  case "$1" in
    --strict-target) strict_target=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

note() { printf '%-10s %s\n' "$1" "$2"; }
warn() { note "WARNING" "$1"; ((warning_count += 1)); }

version_at_least() {
  dpkg --compare-versions "$1" ge "$2"
}

check_target() {
  local os_id="unknown" os_version="unknown" dpkg_arch uname_arch
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-unknown}"
    os_version="${VERSION_ID:-unknown}"
  fi
  dpkg_arch="$(dpkg --print-architecture 2>/dev/null || printf unknown)"
  uname_arch="$(uname -m 2>/dev/null || printf unknown)"

  note "TARGET" "artifact: ${ARTIFACT}"
  if [[ "$os_id" == "$TARGET_OS_ID" && "$os_version" == "$TARGET_OS_VERSION" ]]; then
    note "OK" "OS: ${os_id} ${os_version}"
  else
    warn "OS: found ${os_id} ${os_version}; expected ${TARGET_OS_ID} ${TARGET_OS_VERSION}"
  fi
  if [[ "$dpkg_arch" == "$TARGET_ARCH" && "$uname_arch" == "aarch64" ]]; then
    note "OK" "architecture: ${dpkg_arch} (${uname_arch})"
  else
    warn "architecture: dpkg=${dpkg_arch}, uname=${uname_arch}; expected arm64/aarch64"
  fi
  if [[ -r /etc/nv_tegra_release ]]; then
    note "OK" "Orin/Jetson marker: /etc/nv_tegra_release"
  else
    warn "Orin/Jetson marker is absent: /etc/nv_tegra_release"
  fi
}

check_package() {
  local package="$1" minimum_version="$2" installed_version candidate
  installed_version="$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null || true)"
  if [[ "$installed_version" == "install ok installed "* ]]; then
    installed_version="${installed_version#install ok installed }"
    if [[ "$minimum_version" == "0" ]] || version_at_least "$installed_version" "$minimum_version"; then
      note "INSTALLED" "${package} (${installed_version})"
      ((installed_count += 1))
      return
    fi
    note "OUTDATED" "${package} (${installed_version}; need >= ${minimum_version})"
    ((outdated_count += 1))
    return
  fi

  candidate="$(apt-cache policy "$package" 2>/dev/null | awk '/Candidate:/ { print $2; exit }')"
  if [[ -z "$candidate" || "$candidate" == "(none)" ]]; then
    note "UNAVAILABLE" "${package} (no APT candidate)"
    ((unavailable_count += 1))
  else
    note "MISSING" "${package} (APT candidate: ${candidate})"
    ((missing_count += 1))
  fi
}

main() {
  # package<TAB>minimum-version; keep this list aligned with apt-packages.tsv.
  local packages=(
    'libyaml-cpp0.8	0.8.0'
  )
  local entry package minimum_version

  check_target
  printf '\nDependency status:\n'
  for entry in "${packages[@]}"; do
    entry="${entry//\\t/$'\t'}"
    IFS=$'\t' read -r package minimum_version <<<"$entry"
    check_package "$package" "$minimum_version"
  done

  printf '\nSummary: installed=%d missing=%d outdated=%d unavailable=%d warnings=%d\n' \
    "$installed_count" "$missing_count" "$outdated_count" "$unavailable_count" "$warning_count"
  if ((missing_count || outdated_count || unavailable_count)); then
    echo "RESULT: environment is not ready; install or make available the reported dependencies."
    return 1
  fi
  if [[ "$strict_target" == true && $warning_count -gt 0 ]]; then
    echo "RESULT: dependencies are present, but this is not the requested target environment."
    return 1
  fi
  echo "RESULT: all checked dependencies are installed."
}

main
