#!/bin/bash

# Shared ZJ humanoid host environment for Pico and Orin.
export ZJ_PROFILE_VERSION="V0.0.5"

# Values explicitly supplied by the invoking shell have highest priority.
_MANUAL_ZJ_DEVICE="${ZJ_DEVICE:-}"
_MANUAL_ROBOT_TYPE="${ROBOT_TYPE:-}"
_MANUAL_ROBOT_NAME="${ROBOT_NAME:-}"
_MANUAL_ZJ_VERSION="${ZJ_VERSION:-}"
_MANUAL_ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-}"
# A URI written by an earlier profile run is not a user override. This avoids
# treating an old automatic default as an explicit value when the file vanished.
if [[ "${ZJ_PROFILE_MANAGED_CYCLONEDDS_URI:-0}" == "1" ]]; then
    _MANUAL_CYCLONEDDS_URI=""
else
    _MANUAL_CYCLONEDDS_URI="${CYCLONEDDS_URI:-}"
fi
unset ZJ_PROFILE_MANAGED_CYCLONEDDS_URI
_MANUAL_COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

add_path() {
    local dir="$1"
    [[ -n "$dir" && -d "$dir" ]] || return
    [[ ":$PATH:" == *":$dir:"* ]] || export PATH="$dir:$PATH"
}

export ROS_DOMAIN_ID=72
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

if id "nav01" &>/dev/null; then
    _ZJ_DETECTED_DEVICE="PICO"
else
    _ZJ_DETECTED_DEVICE="ORIN"
fi
_ZJ_DEFAULT_CYCLONEDDS_URI="file:///etc/zj_humanoid/cyclonedds.xml"

_ZJ_ENV_FILE="${ZJ_DEVICE_CONFIG_FILE:-/etc/zj_humanoid/device.env}"

load_device_config() {
    [[ -r "${_ZJ_ENV_FILE}" ]] || return
    local line key value
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -z "${line}" || "${line}" == \#* ]] && continue
        [[ "${line}" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        case "${key}" in
            ZJ_DEVICE)
                [[ "${value}" == "ORIN" || "${value}" == "PICO" ]] && export ZJ_DEVICE="${value}"
                ;;
            ROBOT_TYPE)
                [[ "${value}" =~ ^[A-Za-z0-9_-]+$ ]] && export ROBOT_TYPE="${value}"
                ;;
            ROBOT_NAME|ZJ_VERSION)
                [[ "${value}" =~ ^[A-Za-z0-9._-]*$ ]] && export "${key}=${value}"
                ;;
            ROS_DOMAIN_ID)
                [[ "${value}" =~ ^[0-9]+$ && "${value}" -le 232 ]] && export ROS_DOMAIN_ID="${value}"
                ;;
            CYCLONEDDS_URI)
                [[ "${value}" == file:///* ]] && export CYCLONEDDS_URI="${value}"
                ;;
            COMPOSE_PROFILES)
                [[ "${value}" =~ ^[A-Za-z0-9,_-]*$ ]] && export COMPOSE_PROFILES="${value}"
                ;;
        esac
    done < "${_ZJ_ENV_FILE}"
}

load_device_config
export ZJ_DEVICE="${ZJ_DEVICE:-${_ZJ_DETECTED_DEVICE}}"
[[ -n "${_MANUAL_ZJ_DEVICE}" ]] && export ZJ_DEVICE="${_MANUAL_ZJ_DEVICE}"
[[ -n "${_MANUAL_ROBOT_TYPE}" ]] && export ROBOT_TYPE="${_MANUAL_ROBOT_TYPE}"
[[ -n "${_MANUAL_ROBOT_NAME}" ]] && export ROBOT_NAME="${_MANUAL_ROBOT_NAME}"
[[ -n "${_MANUAL_ZJ_VERSION}" ]] && export ZJ_VERSION="${_MANUAL_ZJ_VERSION}"
[[ -n "${_MANUAL_ROS_DOMAIN_ID}" ]] && export ROS_DOMAIN_ID="${_MANUAL_ROS_DOMAIN_ID}"
[[ -n "${_MANUAL_CYCLONEDDS_URI}" ]] && export CYCLONEDDS_URI="${_MANUAL_CYCLONEDDS_URI}"
[[ -n "${_MANUAL_COMPOSE_PROFILES}" ]] && export COMPOSE_PROFILES="${_MANUAL_COMPOSE_PROFILES}"
if [[ -n "${_MANUAL_ROBOT_TYPE}" && -z "${_MANUAL_COMPOSE_PROFILES}" ]]; then
    unset COMPOSE_PROFILES
fi

if [[ -n "${CYCLONEDDS_URI:-}" && ! -f "${CYCLONEDDS_URI#file://}" ]]; then
    echo "Navi environment: CYCLONEDDS_URI file is unavailable (${CYCLONEDDS_URI}); using CycloneDDS defaults." >&2
    unset CYCLONEDDS_URI
fi
if [[ -z "${CYCLONEDDS_URI:-}" && -f "${_ZJ_DEFAULT_CYCLONEDDS_URI#file://}" ]]; then
    export CYCLONEDDS_URI="${_ZJ_DEFAULT_CYCLONEDDS_URI}"
    export ZJ_PROFILE_MANAGED_CYCLONEDDS_URI=1
fi

export ROBOT_TYPE="${ROBOT_TYPE:-}"
export ROBOT_NAME="${ROBOT_NAME:-}"
export ZJ_VERSION="${ZJ_VERSION:-}"

case "${ROBOT_TYPE}" in
    H1|U1|U2_WA1|I2|WA1|WA1_400L|WA1_400K|WA2_LS|I2-S|I2-D|I2-E|I3-S|WA1-S|WA1-D|WA1-E|WA2-S|WA2-P|WA2-D|U2-S|U2-D|ZYD|JK)
        export ZJ_ROBOT_TYPE_CONFIGURED=1
        ;;
    *)
        export ZJ_ROBOT_TYPE_CONFIGURED=0
        unset COMPOSE_PROFILES
        echo "Navi environment: ROBOT_TYPE is unset or unsupported. Set a supported model in ${_ZJ_ENV_FILE}, or run: export ROBOT_TYPE=<model>; source /etc/profile.d/zj_humanoid.sh" >&2
        return 1 2>/dev/null || exit 1
        ;;
esac

# PICO/env.sh may request only the validated common ROS 2 transport values.
if [[ "${ZJ_PROFILE_ENV_ONLY:-0}" == "1" ]]; then
    return 0 2>/dev/null || exit 0
fi

if [[ "${ZJ_DEVICE}" == "PICO" ]]; then
    export ZJ_ROS_DISTRO="humble"
    [[ -f /opt/ros/humble/setup.bash ]] && source /opt/ros/humble/setup.bash
    export PATH="/home/nav01/.local/bin:$PATH"
    add_path "/home/nav01/.zj_humanoid/bin"
else
    _ZJ_OS_VERSION=""
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        _ZJ_OS_VERSION="${VERSION_ID:-}"
    fi
    case "${_ZJ_OS_VERSION}" in
        22.04)
            export ZJ_ROS_DISTRO="humble"
            export SENSOR_IMAGE_TAG="22CUDA_v1"
            ;;
        24.04)
            export ZJ_ROS_DISTRO="jazzy"
            export SENSOR_IMAGE_TAG="NOCUDA"
            ;;
        *)
            export ZJ_ROS_DISTRO=""
            export SENSOR_IMAGE_TAG="NOCUDA"
            echo "Navi environment: unsupported Orin Ubuntu version ${_ZJ_OS_VERSION:-unknown}; ROS setup was not loaded." >&2
            ;;
    esac
    [[ -n "${ZJ_ROS_DISTRO}" && -f "/opt/ros/${ZJ_ROS_DISTRO}/setup.bash" ]] && source "/opt/ros/${ZJ_ROS_DISTRO}/setup.bash"
    add_path "/home/naviai/.zj_humanoid/bin"
fi

if [[ "${ZJ_DEVICE}" == "ORIN" ]]; then
    export COMPOSE_PROJECT_NAME="navi_project"
    if [[ -z "${COMPOSE_PROFILES:-}" ]]; then
        case "${ROBOT_TYPE}" in
            WA1|WA1_400K|WA1_400L|WA1-S|WA1-D|WA1-E) export COMPOSE_PROFILES="wa1" ;;
            WA2_LS|WA2-S|WA2-P|WA2-D) export COMPOSE_PROFILES="wa2" ;;
            ZYD|JK) export COMPOSE_PROFILES="zyd" ;;
            H1|U1|U2_WA1|U2-S|U2-D) export COMPOSE_PROFILES="h1" ;;
            I2|I2-S|I2-D|I2-E) export COMPOSE_PROFILES="rx" ;;
            I3-S) export COMPOSE_PROFILES="i3" ;;
            *) export COMPOSE_PROFILES="" ;;
        esac
    fi
fi
