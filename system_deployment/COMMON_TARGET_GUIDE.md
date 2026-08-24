# Common 离线包：按目标构建与部署手册

本手册分别给出三个 common deb 的完整操作流程。在线构建机必须是对应目标的原生
Ubuntu 版本和 CPU 架构，并能从 APT 下载清单中的包；离线设备只接收构建出的一个 deb，
不需要 `projects` 源码。

| target | 在线构建机 / 离线设备 | ROS 2 | artifact |
| --- | --- | --- | --- |
| `orin-jazzy` | Orin arm64, Ubuntu 24.04 | Jazzy | `navi_common_dep-2.0.0-release-jazzy-arm64.deb` |
| `orin-humble` | Orin arm64, Ubuntu 22.04 | Humble | `navi_common_dep-2.0.0-release-humble-arm64.deb` |
| `pico-humble` | Pico amd64, Ubuntu 20.04 | Humble | `navi_pico_common_dep-2.0.0-release-humble-amd64.deb` |

## 0. 向在线构建机传输源码

在开发机：

```bash
cd ~
tar -czf projects.tar.gz projects
scp ~/projects.tar.gz <build-user>@<online-build-host>:/tmp/
```

在目标对应的在线构建机：

```bash
cd /tmp
tar -xzf projects.tar.gz
python3 /tmp/projects/system_deployment/common/deploy_common.py show-targets
```

每个 target 必须在自己的原生环境构建，不能用 amd64 PC 构建 arm64 Orin deb，也不能在
24.04/Jazzy 上替代 22.04/Humble 的构建环境。

## 1. Orin Ubuntu 24.04 / ROS 2 Jazzy

### 在线构建

```bash
python3 /tmp/projects/system_deployment/common/deploy_common.py build \
  --target orin-jazzy
```

产物：

```text
/tmp/projects/dist/common/orin/base/navi_common_dep-2.0.0-release-jazzy-arm64.deb
```

当前 payload 包含 `libyaml-cpp0.8` 与 `spdlog (>= 1.9.2)` 的开发/运行时依赖，carrier
内部包名为 `navi-common-dep`。

### 传输到离线 Orin

```bash
scp /tmp/projects/dist/common/orin/base/navi_common_dep-2.0.0-release-jazzy-arm64.deb \
  naviai@<offline-orin>:/tmp/
```

### 离线安装和机型配置

```bash
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-jazzy-arm64.deb

sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-jazzy \
  --robot-type I3-S \
  --robot-name robot-001 \
  --version 2.0.0 \
  --ros-domain-id 72

sudo /usr/sbin/install_common_deps.sh
```

将 `I3-S`、`robot-001`、`2.0.0` 改为实际值。未设置合法 `ROBOT_TYPE` 时，最后一条
命令会失败且不安装 payload。`--robot-name` 和 `--version` 可直接省略，唯一必填的
身份参数是 `--robot-type`。

### 验证

```bash
source /etc/profile.d/zj_humanoid.sh
printf 'device=%s distro=%s type=%s compose=%s\n' \
  "$ZJ_DEVICE" "$ZJ_ROS_DISTRO" "$ROBOT_TYPE" "$COMPOSE_PROFILES"
ros2 topic list
```

预期 `ZJ_DEVICE=ORIN`、`ZJ_ROS_DISTRO=jazzy`。默认 CycloneDDS XML 固定使用
`192.168.217.0/24`，故 Orin 用于机器人网络的网卡应有该网段地址，例如
`192.168.217.100/24`。新开交互式 Bash 会自动加载环境；已打开的终端需执行一次
`source /etc/profile.d/zj_humanoid.sh`。

## 2. Orin Ubuntu 22.04 / ROS 2 Humble

### 在线构建

```bash
python3 /tmp/projects/system_deployment/common/deploy_common.py build \
  --target orin-humble
```

产物：

```text
/tmp/projects/dist/common/orin/base/navi_common_dep-2.0.0-release-humble-arm64.deb
```

此 target 是精简的 Humble carrier：包含构建工具、Python 构建工具、CycloneDDS、
`libyaml-cpp0.7`、`spdlog (>= 1.9.2)` 等 manifest payload；ROS 直接清单仅保留
`ros-humble-rmw-cyclonedds-cpp`。

### 传输到离线 Orin

```bash
scp /tmp/projects/dist/common/orin/base/navi_common_dep-2.0.0-release-humble-arm64.deb \
  naviai@<offline-orin>:/tmp/
```

### 离线安装和机型配置

```bash
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-humble-arm64.deb

sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-humble \
  --robot-type WA2-P \
  --robot-name robot-002 \
  --version 2.0.0 \
  --ros-domain-id 72

sudo /usr/sbin/install_common_deps.sh
```

示例 `WA2-P` 会自动得到 `COMPOSE_PROFILES=wa2`；实际机型应按现场替换。

### 验证

```bash
source /etc/profile.d/zj_humanoid.sh
printf 'device=%s distro=%s type=%s compose=%s\n' \
  "$ZJ_DEVICE" "$ZJ_ROS_DISTRO" "$ROBOT_TYPE" "$COMPOSE_PROFILES"
ros2 topic list
```

预期 `ZJ_DEVICE=ORIN`、`ZJ_ROS_DISTRO=humble`。机器人网络使用默认 XML 时，也需在
相应网卡配置 `192.168.217.0/24` 地址。

## 3. Pico Ubuntu 20.04 / ROS 2 Humble

### 在线构建

```bash
python3 /tmp/projects/system_deployment/common/deploy_common.py build \
  --target pico-humble
```

产物：

```text
/tmp/projects/dist/common/pico/base/navi_pico_common_dep-2.0.0-release-humble-amd64.deb
```

当前 Pico payload 仅为 `python3-yaml` 与 `python3-psutil`；ROS 和功能模块的其他依赖
由各模块自己的 common package 提供。

### 传输到离线 Pico

```bash
scp /tmp/projects/dist/common/pico/base/navi_pico_common_dep-2.0.0-release-humble-amd64.deb \
  nav01@<offline-pico>:/tmp/
```

### 离线安装和机型配置

```bash
sudo dpkg -i /tmp/navi_pico_common_dep-2.0.0-release-humble-amd64.deb

sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure \
  --target pico-humble \
  --robot-type U2-D \
  --robot-name pico-001 \
  --version 2.0.0 \
  --ros-domain-id 72

sudo /usr/sbin/install_pico_common_deps.sh
```

### 验证

```bash
source /etc/profile.d/zj_humanoid.sh
printf 'device=%s distro=%s type=%s configured=%s\n' \
  "$ZJ_DEVICE" "$ZJ_ROS_DISTRO" "$ROBOT_TYPE" "$ZJ_ROBOT_TYPE_CONFIGURED"
ros2 topic list
```

预期 `ZJ_DEVICE=PICO`、`ZJ_ROS_DISTRO=humble`。Pico 不设置 Orin 的
`COMPOSE_PROFILES`。

## 4. 共同说明

包内的 `/etc/profile.d/zj_humanoid.sh` 只加载 ROS 2，不设置 ROS 1 Master、`ROS_IP`
或 `ROS_HOSTNAME`。机器人参数保存在 `/etc/zj_humanoid/device.env`；配置不会因为
common 包升级而被自动覆盖。

每个 carrier 内有 `manifest.lock.json` 与 `payloads.sha256`。安装器会先校验 SHA256，
通过后才调用 `dpkg -i` 安装 payload。排查时可查看：

```bash
# Orin
cat /usr/lib/navi-common-dep/manifest.lock.json

# Pico
cat /usr/lib/navi-pico-common-dep/manifest.lock.json
```
