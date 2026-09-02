#!/usr/bin/env bash
# Read-only dependency audit for a Pico Ubuntu 24.04 / ROS 2 Jazzy delivery.
#
# The default probe checks only the target's installed environment.  It does
# not require or inspect the upperlimb DEBs that will be installed later.  An
# optional deep scan can read those DEBs without installing them.

set -u -o pipefail

readonly TARGET_OS_ID="ubuntu"
readonly TARGET_OS_VERSION="24.04"
readonly TARGET_ARCH="amd64"
readonly TARGET_MACHINE="x86_64"
readonly TARGET_ROS_DISTRO="jazzy"

strict_target=false
fail_on_missing=false
deb_dir=""

installed_count=0
missing_count=0
version_mismatch_count=0
warning_count=0
bundled_count=0
external_library_count=0
ros_library_count=0
work_dir=""
declare -A bundled_sonames=()
declare -A bundled_packages=()
declare -A seen_dependencies=()
declare -A seen_libraries=()

usage() {
    cat <<'EOF'
Usage: audit_pico_jazzy_dependencies.sh [options]

Read-only audit for the Pico Ubuntu 24.04 / ROS 2 Jazzy upperlimb delivery.
It checks the installed ROS package versions and the key system shared
libraries needed by upperlimb. It does not require the upperlimb DEBs.

Options:
  --deb-dir DIR       Optional deep scan of Jazzy upperlimb *.deb metadata and
                      ELF requirements. The DEBs are read but never installed.
  --strict-target     Return failure unless this is Ubuntu 24.04 amd64/x86_64
                      with /opt/ros/jazzy/setup.bash present.
  --fail-on-missing   Return failure when a package or external shared library
                      is unavailable on the target.
  -h, --help          Show this help.

The script never changes installed packages, APT sources, or system settings.
EOF
}

note() { printf '%-10s %s\n' "$1" "$2"; }
warn() { note "WARNING" "$1"; ((warning_count += 1)); }
missing() { note "MISSING" "$1"; ((missing_count += 1)); }

while (($#)); do
    case "$1" in
        --deb-dir)
            shift
            [[ $# -gt 0 ]] || { echo "ERROR: --deb-dir needs a directory" >&2; exit 2; }
            deb_dir="$1"
            ;;
        --strict-target) strict_target=true ;;
        --fail-on-missing) fail_on_missing=true ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

check_command() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "ERROR: required command is unavailable: $1" >&2
        exit 2
    }
}

check_target() {
    local os_id="unknown" os_version="unknown" dpkg_arch uname_arch ros_setup
    if [[ -r /etc/os-release ]]; then
        os_id="$(awk -F= '$1 == "ID" { gsub(/"/, "", $2); print $2; exit }' /etc/os-release)"
        os_version="$(awk -F= '$1 == "VERSION_ID" { gsub(/"/, "", $2); print $2; exit }' /etc/os-release)"
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

apt_candidate() {
    apt-cache policy "$1" 2>/dev/null | awk '/Candidate:/ { print $2; exit }'
}

check_debian_package() {
    local package="$1" purpose="$2" required_op="${3:-}" required_version="${4:-}"
    local installed candidate installed_version constraint candidate_status
    [[ -n "${seen_dependencies[$package]:-}" ]] && return
    seen_dependencies["$package"]=1

    # Input DEBs are the payload to be installed after this audit.  Do not
    # require them to be installed on the target beforehand.
    if [[ -n "${bundled_packages[$package]:-}" ]]; then
        return
    fi

    constraint=""
    [[ -n "$required_op" ]] && constraint="; requires ${required_op} ${required_version}"

    installed="$(dpkg-query -W -f='${Status} ${Version}' "$package" 2>/dev/null || true)"
    if [[ "$installed" == "install ok installed "* ]]; then
        installed_version="${installed#install ok installed }"
        if [[ -n "$required_op" ]] && ! dpkg --compare-versions "$installed_version" "$required_op" "$required_version"; then
            note "VERSION" "${package} (${installed_version}${constraint}; ${purpose})"
            ((version_mismatch_count += 1))
        else
            note "INSTALLED" "${package} (${installed_version}${constraint}; ${purpose})"
        fi
        ((installed_count += 1))
        return
    fi

    candidate="$(apt_candidate "$package")"
    if [[ -n "$candidate" && "$candidate" != "(none)" ]]; then
        candidate_status=""
        if [[ -n "$required_op" ]] && ! dpkg --compare-versions "$candidate" "$required_op" "$required_version"; then
            candidate_status="; candidate does not satisfy ${required_op} ${required_version}"
        fi
        missing "${package} (APT candidate: ${candidate}${constraint}${candidate_status}; ${purpose})"
    else
        missing "${package} (no APT candidate${constraint}; ${purpose})"
    fi
}

dependency_spec() {
    # Convert one comma-separated Debian dependency term into its first
    # alternative package name and optional version constraint. Alternatives
    # are reported through their first candidate, matching dpkg's ordering.
    local term="$1"
    term="${term%%|*}"
    term="$(printf '%s' "$term" | xargs)"
    if [[ "$term" == *'('*')'* ]]; then
        printf '%s\n' "$term" | sed -nE 's/^([A-Za-z0-9][A-Za-z0-9+.-]*)(:[A-Za-z0-9-]+)?[[:space:]]*\((<<|<=|=|>=|>>)[[:space:]]*([^)]*)\).*/\1\t\3\t\4/p'
    else
        printf '%s\n' "$term" | sed -nE 's/^([A-Za-z0-9][A-Za-z0-9+.-]*).*/\1\t\t/p'
    fi
}

collect_bundled_sonames() {
    local extraction_dir="$1" candidate name
    while IFS= read -r -d '' candidate; do
        name="$(basename "$candidate")"
        [[ "$name" == *.so || "$name" == *.so.* ]] && bundled_sonames["$name"]=1
    done < <(find "$extraction_dir" \( -type f -o -type l \) -print0)
}

library_provider() {
    local path="$1" resolved_path owner
    for resolved_path in "$path" "$(readlink -f "$path" 2>/dev/null || true)"; do
        [[ -n "$resolved_path" ]] || continue
        owner="$(dpkg-query -S "$resolved_path" 2>/dev/null | head -n 1 || true)"
        if [[ -n "$owner" ]]; then
            printf '%s' "${owner%%:*}"
            return
        fi
    done
    printf unknown
}

check_shared_library() {
    local soname="$1" system_path ros_path provider
    [[ -n "${seen_libraries[$soname]:-}" ]] && return
    seen_libraries["$soname"]=1

    if [[ -n "${bundled_sonames[$soname]:-}" ]]; then
        note "BUNDLED" "${soname} (provided by an input DEB)"
        ((bundled_count += 1))
        return
    fi

    # ROS deliberately installs its shared objects below /opt rather than a
    # loader-cache directory.  Check the selected Jazzy prefix explicitly so
    # these dependencies are not misclassified as third-party missing libs.
    ros_path="/opt/ros/${TARGET_ROS_DISTRO}/lib/${soname}"
    if [[ -e "$ros_path" ]]; then
        provider="$(library_provider "$ros_path")"
        note "ROS" "${soname} (${ros_path}; Debian provider: ${provider})"
        ((ros_library_count += 1))
        return
    fi

    system_path="$(LC_ALL=C ldconfig -p 2>/dev/null | awk -v soname="$soname" '$1 == soname { print $NF; exit }')"
    if [[ -n "$system_path" && -e "$system_path" ]]; then
        provider="$(library_provider "$system_path")"
        note "SYSTEM" "${soname} (${system_path}; Debian provider: ${provider})"
        ((external_library_count += 1))
        return
    fi

    missing "${soname} (not bundled and absent from ldconfig cache)"
}

inspect_elfs() {
    local extraction_dir="$1" elf soname
    while IFS= read -r -d '' elf; do
        LC_ALL=C readelf -h "$elf" >/dev/null 2>&1 || continue
        while IFS= read -r soname; do
            [[ -n "$soname" ]] && check_shared_library "$soname"
        done < <(LC_ALL=C readelf -d "$elf" 2>/dev/null | sed -n 's/.*Shared library: \[\(.*\)\]/\1/p')
    done < <(find "$extraction_dir" -type f -print0)
}

main() {
    local deb package version depends entry package_name required_op required_version extraction_dir
    local dependency_entry library_entry
    local -a debs=()
    local -a expected_packages=(
        $'python3-yaml\tshared YAML configuration utility'
        $'python3-psutil\tshared resource reporting utility'
        $'ros-jazzy-ament-index-cpp\tupperlimb ROS runtime'
        $'ros-jazzy-geometry-msgs\tupperlimb ROS messages'
        $'ros-jazzy-kdl-parser\tupperlimb kinematics parser'
        $'ros-jazzy-launch\tupperlimb launcher'
        $'ros-jazzy-launch-ros\tupperlimb ROS launch integration'
        $'ros-jazzy-rclcpp\tupperlimb ROS C++ runtime'
        $'ros-jazzy-sensor-msgs\tupperlimb ROS messages'
        $'ros-jazzy-std-msgs\tupperlimb ROS messages'
        $'ros-jazzy-std-srvs\tupperlimb ROS services'
        $'ros-jazzy-tf2-ros\tupperlimb transforms'
        $'ros-jazzy-urdf\tupperlimb robot model parser'
        $'ros-jazzy-action-msgs\tupperlimb action messages'
        $'ros-jazzy-rosidl-default-runtime\tupperlimb ROS interface runtime'
    )
    local -a expected_libraries=(
        libzmq.so.5
        libhdf5_serial.so.103
        libhdf5_serial_cpp.so.103
        libpinocchio_parsers.so.3.4.0
        libtinyxml2.so.6
        libalchemy.so.0
        libcopperplate.so.0
        libcobalt.so.2
        libmodechk.so.0
    )

    check_command apt-cache
    check_command dpkg
    check_command dpkg-query
    check_command ldconfig
    if [[ -n "$deb_dir" ]]; then
        check_command dpkg-deb
        check_command readelf
    fi
    check_target

    printf '\nInstalled environment package requirements:\n'
    for dependency_entry in "${expected_packages[@]}"; do
        IFS=$'\t' read -r package_name entry <<<"$dependency_entry"
        check_debian_package "$package_name" "$entry"
    done

    printf '\nThird-party shared-library requirements:\n'
    for library_entry in "${expected_libraries[@]}"; do
        check_shared_library "$library_entry"
    done

    if [[ -n "$deb_dir" ]]; then
        [[ -d "$deb_dir" ]] || { echo "ERROR: DEB directory is unavailable: $deb_dir" >&2; return 2; }
        shopt -s nullglob
        debs=("$deb_dir"/*.deb)
        shopt -u nullglob
        ((${#debs[@]})) || { echo "ERROR: no .deb files found in $deb_dir" >&2; return 2; }

    for deb in "${debs[@]}"; do
        package="$(dpkg-deb -f "$deb" Package 2>/dev/null || true)"
        [[ -n "$package" ]] && bundled_packages["$package"]=1
    done

    work_dir="$(mktemp -d "${TMPDIR:-/tmp}/pico-jazzy-audit.XXXXXX")"
    trap 'rm -rf -- "$work_dir"' EXIT

    printf '\nOptional input-DEB dependency scan:\n'
    for deb in "${debs[@]}"; do
        package="$(dpkg-deb -f "$deb" Package 2>/dev/null || true)"
        version="$(dpkg-deb -f "$deb" Version 2>/dev/null || true)"
        [[ -n "$package" ]] || { warn "cannot read Debian control metadata: $deb"; continue; }
        note "INPUT" "${package} (${version}; metadata only, not checked as installed)"
        depends="$(dpkg-deb -f "$deb" Depends 2>/dev/null || true)"
        IFS=',' read -r -a entries <<<"$depends"
        for entry in "${entries[@]}"; do
            IFS=$'\t' read -r package_name required_op required_version <<<"$(dependency_spec "$entry" || true)"
            [[ -n "$package_name" ]] && check_debian_package "$package_name" "required by ${package}" "$required_op" "$required_version"
        done

        extraction_dir="${work_dir}/$(basename "$deb" .deb)"
        mkdir -p "$extraction_dir"
        dpkg-deb -x "$deb" "$extraction_dir"
    done

        collect_bundled_sonames "$work_dir"
        printf '\nELF shared-library requirements:\n'
        inspect_elfs "$work_dir"
    fi

    printf '\nSummary: installed-packages=%d version-mismatches=%d missing=%d bundled-libraries=%d ROS-libraries=%d system-libraries=%d warnings=%d\n' \
        "$installed_count" "$version_mismatch_count" "$missing_count" "$bundled_count" "$ros_library_count" "$external_library_count" "$warning_count"
    echo "NEXT: Use only the SYSTEM library provider packages and confirmed missing APT candidates when writing Pico Jazzy common manifests."

    if [[ "$strict_target" == true && $warning_count -gt 0 ]]; then
        return 1
    fi
    if [[ "$fail_on_missing" == true && $missing_count -gt 0 ]]; then
        return 1
    fi
}

main
