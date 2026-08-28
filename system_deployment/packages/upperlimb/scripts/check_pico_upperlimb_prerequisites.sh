#!/usr/bin/env bash
# Read-only preflight check for the Pico upperlimb delivery.
# It neither installs packages nor changes any persistent system setting.

set -uo pipefail

failures=0
warnings=0

pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*" >&2; failures=$((failures + 1)); }
warn() { printf 'WARN  %s\n' "$*" >&2; warnings=$((warnings + 1)); }

installed_deb() {
    local package_name=$1
    dpkg-query -W -f='${db:Status-Status}' "$package_name" 2>/dev/null | grep -qx installed
}

cached_library() {
    local library_name=$1
    ldconfig -p 2>/dev/null | awk '{print $1}' | grep -Fxq "$library_name"
}

printf '%s\n' 'Pico upperlimb preflight check'
printf '%s\n' 'Expected target: Ubuntu 20.04, amd64, ROS 2 Humble, ROS_DOMAIN_ID=72'

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    if [ "${ID:-}" = ubuntu ] && [ "${VERSION_ID:-}" = 20.04 ]; then
        pass "operating system is Ubuntu 20.04"
    else
        fail "expected Ubuntu 20.04, found ${ID:-unknown} ${VERSION_ID:-unknown}"
    fi
else
    fail '/etc/os-release is unavailable'
fi

if [ "$(dpkg --print-architecture 2>/dev/null || true)" = amd64 ] && [ "$(uname -m)" = x86_64 ]; then
    pass 'architecture is amd64/x86_64'
else
    fail "expected amd64/x86_64, found dpkg=$(dpkg --print-architecture 2>/dev/null || printf unknown) uname=$(uname -m)"
fi

if installed_deb navi-pico-common-dep; then
    pass 'navi-pico-common-dep is installed'
else
    fail 'navi-pico-common-dep is not installed; it provides the shared Pico environment'
fi

if installed_deb navi-pico-upperlimb-common-dep; then
    pass 'navi-pico-upperlimb-common-dep is installed'
else
    warn 'navi-pico-upperlimb-common-dep is not installed; install it before the upperlimb run package'
fi

if [ -r /etc/nav01/Middleware.env ]; then
    pass '/etc/nav01/Middleware.env is readable'
else
    fail '/etc/nav01/Middleware.env is missing or unreadable'
fi

if [ -r /etc/zj_humanoid/cyclonedds.xml ]; then
    pass '/etc/zj_humanoid/cyclonedds.xml is readable'
else
    fail '/etc/zj_humanoid/cyclonedds.xml is missing or unreadable'
fi

if [ -r /opt/ros/humble/setup.bash ]; then
    pass '/opt/ros/humble/setup.bash is readable'
else
    fail '/opt/ros/humble/setup.bash is missing or unreadable'
fi

ros_packages=(
    ros-humble-action-msgs
    ros-humble-ament-index-cpp
    ros-humble-geometry-msgs
    ros-humble-kdl-parser
    ros-humble-launch
    ros-humble-launch-ros
    ros-humble-rclcpp
    ros-humble-rosidl-default-runtime
    ros-humble-sensor-msgs
    ros-humble-std-msgs
    ros-humble-std-srvs
    ros-humble-tf2-ros
    ros-humble-urdf
)
for package_name in "${ros_packages[@]}"; do
    if installed_deb "$package_name"; then
        pass "$package_name is installed"
    else
        fail "$package_name is not installed"
    fi
done

# These are required by the supplied upperlimb ELF binaries but are not
# declared in their Debian Depends fields.  They are delivered by
# navi-pico-upperlimb-common-dep, not by the five upperlimb input packages.
for library_name in libzmq.so.5 libhdf5_cpp.so.103 libhdf5_serial.so.103; do
    if cached_library "$library_name"; then
        pass "$library_name is available through ldconfig"
    else
        fail "$library_name is unavailable through ldconfig"
    fi
done

if command -v systemctl >/dev/null 2>&1 && [ "$(ps -p 1 -o comm= 2>/dev/null | tr -d '[:space:]')" = systemd ] && systemctl show-environment >/dev/null 2>&1; then
    pass 'systemd is reachable for the auto-start service'
else
    fail 'systemd is not reachable as PID 1; automatic service startup cannot be configured in this environment'
fi

if command -v taskset >/dev/null 2>&1; then
    if taskset -c 9 /bin/true 2>/dev/null; then
        pass 'CPU 9 can be selected with taskset'
    else
        fail 'CPU 9 cannot be selected with taskset'
    fi
else
    fail 'taskset is unavailable (install util-linux)'
fi

if [ -r /etc/nav01/Middleware.env ]; then
    environment_output=$(bash -c '. /etc/nav01/Middleware.env >/dev/null && printf "%s|%s|%s|%s" "${MIDDLEWARE_PLATFORM:-}" "${MIDDLEWARE_ROS_DISTRO:-}" "${ROS_DISTRO:-}" "${ROS_DOMAIN_ID:-}"' 2>&1) || true
    environment_values=$(printf '%s\n' "$environment_output" | tail -n 1)
    IFS='|' read -r middleware_platform middleware_ros_distro ros_distro ros_domain_id <<< "$environment_values"
    if [ "$middleware_platform" = PICO ]; then
        pass 'Middleware environment reports platform PICO'
    else
        fail "Middleware environment reports platform ${middleware_platform:-unknown}"
    fi
    if [ "$middleware_ros_distro" = humble ] && [ "$ros_distro" = humble ]; then
        pass 'Middleware environment loads ROS 2 Humble'
    else
        fail "Middleware environment did not load Humble (MIDDLEWARE_ROS_DISTRO=${middleware_ros_distro:-unset}, ROS_DISTRO=${ros_distro:-unset})"
    fi
    if [ "$ros_domain_id" = 72 ]; then
        pass 'ROS_DOMAIN_ID is 72'
    else
        fail "expected ROS_DOMAIN_ID=72, found ${ros_domain_id:-unset}"
    fi
    if printf '%s\n' "$environment_output" | grep -q 'device profile is not configured\|ROBOT_TYPE is unset or unsupported'; then
        warn 'Pico device profile has no valid ROBOT_TYPE; configure it before starting upperlimb'
    fi
fi

printf '\nSummary: %d failure(s), %d warning(s)\n' "$failures" "$warnings"
if [ "$failures" -ne 0 ]; then
    exit 1
fi
