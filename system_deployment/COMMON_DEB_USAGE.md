# Navi Common Deb 使用手册

本手册只说明如何在目标设备上使用已收到的 common deb。

## 1. 选择正确的 deb

| 目标设备 | 使用的 deb | 内部包名 | payload 安装命令 |
| --- | --- | --- | --- |
| Orin Ubuntu 24.04 / ROS 2 Jazzy / arm64 | `navi_common_dep-2.0.0-release-jazzy-arm64.deb` | `navi-common-dep` | `/usr/sbin/install_common_deps.sh` |
| Orin Ubuntu 22.04 / ROS 2 Humble / arm64 | `navi_common_dep-2.0.0-release-humble-arm64.deb` | `navi-common-dep` | `/usr/sbin/install_common_deps.sh` |
| Pico Ubuntu 20.04 / ROS 2 Humble / amd64 | `navi_pico_common_dep-2.0.0-release-humble-amd64.deb` | `navi-pico-common-dep` | `/usr/sbin/install_pico_common_deps.sh` |

只能在表中对应的设备、Ubuntu 版本和 CPU 架构上安装。安装前可检查 deb 信息：

```bash
dpkg-deb -f /tmp/<common-deb>.deb Package Version Architecture
```

## 2. 安装 Orin Jazzy 或 Orin Humble 包

以下命令中的文件名和 target 必须二选一匹配。

### Orin Ubuntu 24.04 / Jazzy

```bash
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-jazzy-arm64.deb

# 仅首次配置、机型变更或修复配置时执行：
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-jazzy \
  --robot-type I3-S

sudo /usr/sbin/install_common_deps.sh
```

### Orin Ubuntu 22.04 / Humble

```bash
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-humble-arm64.deb

# 仅首次配置、机型变更或修复配置时执行：
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-humble \
  --robot-type WA2-P

sudo /usr/sbin/install_common_deps.sh
```

如果 `/etc/zj_humanoid/device.env` 已有正确的机型配置，跳过 `configure`，直接执行
payload 安装器即可。可先验证已有配置：

```bash
sudo python3 /usr/lib/navi-common-dep/deploy_common.py validate-config \
  --target orin-jazzy   # Humble 设备改为 orin-humble
```

首次配置时 `--robot-type` 必填，示例机型须替换为真实机型。`--robot-name`、`--version`、
`--ros-domain-id` 均可省略；`ROS_DOMAIN_ID` 默认值为 `72`。

## 3. 安装 Pico Humble 包

```bash
sudo dpkg -i /tmp/navi_pico_common_dep-2.0.0-release-humble-amd64.deb

# 仅首次配置、机型变更或修复配置时执行：
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure \
  --target pico-humble \
  --robot-type U2-D

sudo /usr/sbin/install_pico_common_deps.sh
```

Pico 已有有效机型配置时同样可跳过 `configure`。首次配置只要求 `--robot-type`；
`ROBOT_NAME` 和 `ZJ_VERSION` 可选。

## 4. 机器人型号

支持的 `ROBOT_TYPE`：

```text
H1  U1  U2_WA1  I2  WA1  WA1_400L  WA1_400K  WA2_LS
I2-S  I2-D  I2-E  I3-S
WA1-S  WA1-D  WA1-E
WA2-S  WA2-P  WA2-D
U2-S  U2-D
ZYD  JK
```

Orin 会根据机型自动设置 `COMPOSE_PROFILES`，例如 `I3-S → i3`、`I2-D → rx`、
`WA2-P → wa2`、`U2-D → h1`。Pico 不设置 Orin Compose profile。

配置保存在 `/etc/zj_humanoid/device.env`。修改机型时再次执行对应的 `configure` 命令即可；
不需要重新安装 deb。

## 5. 环境自动加载与验证

安装后，新开启的交互式 Bash 会自动加载环境。当前已经打开的终端不能被安装程序直接修改，
请执行：

```bash
exec bash
```

验证：

```bash
printf 'device=%s distro=%s type=%s version=%s compose=%s\n' \
  "$ZJ_DEVICE" "$ZJ_ROS_DISTRO" "$ROBOT_TYPE" "$ZJ_VERSION" \
  "${COMPOSE_PROFILES:-}"

ros2 topic list
```

预期：Orin 24.04 为 `ZJ_ROS_DISTRO=jazzy`；Orin 22.04 与 Pico 20.04 为
`ZJ_ROS_DISTRO=humble`。

## 6. CycloneDDS 网络配置

包会安装 `/etc/zj_humanoid/cyclonedds.xml`，默认使用 `192.168.217.0/24` 网段并允许
SPDP 组播。环境变量自动设置为：

```text
CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml
```

设备必须有一个已激活网卡配置在该网段。例如 Orin 可配置：

```text
192.168.217.100/24
```

若运行 `ros2 topic list` 出现 `does not match an available interface`，说明该网段地址未在
任何已激活网卡上生效。请先连接机器人网络，并检查网卡地址。

若现场需要不同的 CycloneDDS XML，可在 `/etc/zj_humanoid/device.env` 添加：

```text
CYCLONEDDS_URI=file:///path/to/site-cyclonedds.xml
```

指定的文件必须存在；否则 profile 会告警并回退到包内默认 XML。

## 7. 常见问题

| 现象 | 处理 |
| --- | --- |
| `ROBOT_TYPE must be configured` | 先执行包内 `deploy_common.py configure --target ... --robot-type ...`，再运行 payload 安装器。 |
| 当前终端的 `ROBOT_TYPE` 为空 | 执行 `exec bash`，或重新登录。 |
| `ros2 topic list` 找不到接口 | 检查机器人网卡是否已连接并拥有 `192.168.217.x/24` 地址。 |
| 机型需变更 | 再次执行 `configure` 命令；无需重新安装 deb。 |

## 8. 已安装文件与完整性信息

Orin：

```bash
dpkg-query -W navi-common-dep
cat /usr/lib/navi-common-dep/manifest.lock.json
```

Pico：

```bash
dpkg-query -W navi-pico-common-dep
cat /usr/lib/navi-pico-common-dep/manifest.lock.json
```

payload 安装器在执行安装前会自动校验包内 `payloads.sha256`。
