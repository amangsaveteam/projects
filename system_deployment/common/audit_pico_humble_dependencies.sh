#!/usr/bin/env bash
# Read-only audit for reducing the Pico Ubuntu 20.04 / ROS 2 Humble bundle.
# It never runs apt update/install/remove, sudo, or makes any system change.

set -u -o pipefail

readonly TARGET_OS_ID="ubuntu"
readonly TARGET_OS_VERSION="20.04"
readonly TARGET_ARCH="amd64"
readonly TARGET_MACHINE="x86_64"
readonly TARGET_ROS_DISTRO="humble"

installed_count=0
missing_count=0
warning_count=0
manual_packages=""

usage() {
  cat <<'EOF'
Usage: audit_pico_humble_dependencies.sh [--strict-target]

Audits every package currently listed in the minimal Pico common manifest. For
every installed package it reports its version and whether APT marks it as
manual or automatic.

The script is read-only.  It never runs apt update, apt install, apt remove,
sudo, or any other system-changing command.

Options:
  --strict-target  Return failure when the host is not Ubuntu 20.04 amd64 Pico.
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

check_target() {
  local os_id="unknown" os_version="unknown" dpkg_arch uname_arch ros_setup
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-unknown}"
    os_version="${VERSION_ID:-unknown}"
  fi
  dpkg_arch="$(dpkg --print-architecture 2>/dev/null || printf unknown)"
  uname_arch="$(uname -m 2>/dev/null || printf unknown)"
  ros_setup="/opt/ros/${TARGET_ROS_DISTRO}/setup.bash"

  if [[ "$os_id" == "$TARGET_OS_ID" && "$os_version" == "$TARGET_OS_VERSION" ]]; then
    note "OK" "OS: ${os_id} ${os_version}"
  else
    warn "OS: found ${os_id} ${os_version}; expected ${TARGET_OS_ID} ${TARGET_OS_VERSION}"
  fi
  if [[ "$dpkg_arch" == "$TARGET_ARCH" && "$uname_arch" == "$TARGET_MACHINE" ]]; then
    note "OK" "architecture: ${dpkg_arch} (${uname_arch})"
  else
    warn "architecture: dpkg=${dpkg_arch}, uname=${uname_arch}; expected ${TARGET_ARCH}/${TARGET_MACHINE}"
  fi
  if [[ -r "$ros_setup" ]]; then
    note "OK" "ROS setup file: ${ros_setup}"
  else
    warn "ROS setup file is absent: ${ros_setup}"
  fi
}

installation_mark() {
  if grep -Fqx -- "$1" <<<"$manual_packages"; then
    printf manual
  else
    printf auto
  fi
}

check_package() {
  local package="$1" purpose="$2" version mark candidate
  version="$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null || true)"
  if [[ "$version" == "install ok installed "* ]]; then
    version="${version#install ok installed }"
    mark="$(installation_mark "$package")"
    note "INSTALLED" "${package} (${version}; ${mark}; ${purpose})"
    ((installed_count += 1))
    return
  fi

  candidate="$(apt-cache policy "$package" 2>/dev/null | awk '/Candidate:/ { print $2; exit }')"
  if [[ -z "$candidate" || "$candidate" == "(none)" ]]; then
    note "MISSING" "${package} (no APT candidate; ${purpose})"
  else
    note "MISSING" "${package} (APT candidate: ${candidate}; ${purpose})"
  fi
  ((missing_count += 1))
}

main() {
  # package<TAB>purpose; keep this list aligned with manifests/pico/apt-packages.tsv.
  local packages=(
    $'python3-yaml\tPico YAML configuration'
    $'python3-psutil\tPico resource reporting'
  )
  local entry package purpose

  manual_packages="$(apt-mark showmanual 2>/dev/null || true)"
  check_target
  printf '\nCurrent Pico common manifest:\n'
  for entry in "${packages[@]}"; do
    IFS=$'\t' read -r package purpose <<<"$entry"
    check_package "$package" "$purpose"
  done

  printf '\nSummary: installed=%d missing=%d warnings=%d\n' \
    "$installed_count" "$missing_count" "$warning_count"
  echo "SCOPE: Pico common contains only shared Python utilities; ROS dependencies belong to module-specific common_dep packages."
  if ((missing_count)); then
    return 1
  fi
  if [[ "$strict_target" == true && $warning_count -gt 0 ]]; then
    return 1
  fi
}

main
