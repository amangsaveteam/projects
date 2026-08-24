# Common 离线包：构建、部署与参数配置

本文档是 `system_deployment/common` 的操作入口，覆盖三个受支持 target：

按 target 可直接执行的完整命令见
[COMMON_TARGET_GUIDE.md](COMMON_TARGET_GUIDE.md)。

| target | 构建环境 | ROS 2 | 产物 |
| --- | --- | --- | --- |
| `orin-jazzy` | Orin, Ubuntu 24.04 arm64 | Jazzy | `navi_common_dep-2.0.0-release-jazzy-arm64.deb` |
| `orin-humble` | Orin, Ubuntu 22.04 arm64 | Humble | `navi_common_dep-2.0.0-release-humble-arm64.deb` |
| `pico-humble` | Pico, Ubuntu 20.04 amd64 | Humble | `navi_pico_common_dep-2.0.0-release-humble-amd64.deb` |

## 设计与文件职责

构建配置只定义 target、payload 与产物名称；机器人现场配置不写入构建配置。

```text
target 配置       → 构建哪个平台的依赖包
robot-types.json  → 允许的 ROBOT_TYPE 与 Orin Compose profile
/etc/zj_humanoid/device.env
                 → 某一台真实设备的型号、名称、DDS 参数
/etc/profile.d/zj_humanoid.sh
                 → 读取设备配置并加载对应 ROS 2 环境
manifest.lock.json
                 → 记录离线 payload 的版本、架构与 SHA256
```

环境脚本不会 source 用户可写的 `.env` 文件。它只解析 `/etc/zj_humanoid/device.env`
中的白名单 `KEY=VALUE` 配置，优先级为：当前 Shell 显式 export > `device.env` >
设备探测 fallback。

## 1. 传输源码到在线构建机

在开发机生成压缩包：

```bash
cd ~
tar -czf projects.tar.gz projects
scp ~/projects.tar.gz <user>@<online-build-host>:/tmp/
```

构建机解压：

```bash
cd /tmp
tar -xzf projects.tar.gz
```

构建机必须是对应 target 的原生 OS/架构，且 APT 软件源已可下载全部清单包。不要使用
amd64 PC 构建 arm64 Orin 包。

查看支持 target：

```bash
python3 /tmp/projects/system_deployment/common/deploy_common.py show-targets
```

## 2. 构建三种 common deb

```bash
# Orin Ubuntu 24.04 / Jazzy
python3 /tmp/projects/system_deployment/common/deploy_common.py build --target orin-jazzy

# Orin Ubuntu 22.04 / Humble
python3 /tmp/projects/system_deployment/common/deploy_common.py build --target orin-humble

# Pico Ubuntu 20.04 / Humble
python3 /tmp/projects/system_deployment/common/deploy_common.py build --target pico-humble
```

输出目录：

```text
/tmp/dist/common/orin/base/
/tmp/dist/common/pico/base/
```

三个产物都是具有独立 Debian 包名的 carrier deb：Orin 为 `navi-common-dep`，Pico
为 `navi-pico-common-dep`。它们内嵌原始 apt payload，构建会写入
`manifest.lock.json` 和 `payloads.sha256`，供离线安装前验证。

当前 Jazzy 的 Debian 修订版本为 `2.0.0~jazzy+7`；对外文件名保持
`navi_common_dep-2.0.0-release-jazzy-arm64.deb`，因此交付路径不变。

`/etc/profile.d/zj_humanoid.sh` 是 common 包统一维护的运行脚本，升级时会自动更新；
设备现场参数则独立保存在 conffile `/etc/zj_humanoid/device.env`，不会被升级覆盖。

## 3. 传输与安装

将对应 deb 传至离线设备：

```bash
scp /tmp/dist/common/orin/base/navi_common_dep-2.0.0-release-jazzy-arm64.deb \
  <user>@<offline-device>:/tmp/
```

离线设备只需要接收这一个 deb，不需要 `projects` 源码。安装分为三个明确步骤：先用
`dpkg` 安装 carrier，再写入已校验的机器人型号，最后执行 carrier 安装到设备中的
payload 安装器。该安装器在 `/usr/sbin/`，随 deb 一起安装，并从 `/usr/lib/` 的包内
payload 校验并安装依赖。未配置合法 `ROBOT_TYPE` 时，payload 安装器会退出且不安装依赖。

```bash
# Orin Jazzy 或 Orin Humble
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-<jazzy-or-humble>-arm64.deb
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-jazzy --robot-type I3-S --robot-name robot-001
sudo /usr/sbin/install_common_deps.sh

# Pico Humble
sudo dpkg -i /tmp/navi_pico_common_dep-2.0.0-release-humble-amd64.deb
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure \
  --target pico-humble --robot-type U2-D --robot-name pico-001
sudo /usr/sbin/install_pico_common_deps.sh
```

安装命令会校验 deb 架构。所有 bundle 在安装 payload 前会执行
`sha256sum -c payloads.sha256`；校验失败时不会调用 `dpkg -i`。

不能在 deb 的 `postinst` 中自动执行最后一步：`dpkg` 在执行 maintainer script 期间持有
数据库锁，嵌套再运行 `dpkg -i` 会失败。因此第二步必须由操作者显式运行；但它完全来自
已安装的 common deb，而非开发源码。

## 4. 参数化配置真实设备

安装 carrier 后，使用已安装的 CLI 写入设备配置。以下命令会校验 target、机型、
ROS Domain 与 URI；不会执行 shell 表达式。

`ROBOT_TYPE` 是唯一必填的现场身份参数。`ROBOT_NAME` 与 `ZJ_VERSION` 均为可选；
省略它们不会影响 ROS 2、Compose profile 或 payload 安装。

```bash
# Orin 24.04 的 I3-S
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-jazzy \
  --robot-type I3-S \
  --robot-name robot-001 \
  --version 2.0.0 \
  --ros-domain-id 72

# Pico 的 U2-D
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure \
  --target pico-humble \
  --robot-type U2-D \
  --robot-name pico-001
```

配置写入：

```text
/etc/zj_humanoid/device.env
```

允许的机型由包内 `robot-types.json` 定义。Orin 默认从机型推导 Compose profile，例如
`I2-D → rx`、`WA2-P → wa2`、`U2-S → h1`。如需覆盖，可传入：

```bash
--compose-profiles custom-profile
```

`CYCLONEDDS_URI` 可省略。common 包会安装默认配置
`/etc/zj_humanoid/cyclonedds.xml`，profile 默认导出
`file:///etc/zj_humanoid/cyclonedds.xml`。默认 XML 固定使用 `192.168.217.0/24` 网段
并允许 SPDP 组播；设备必须在连接网关的网卡上配置该网段的有效地址，例如 Orin 使用
`192.168.217.100/24`。若现场必须使用不同网段，可通过 `device.env` 指向现场专用 XML；
覆盖文件不存在时会告警并回退到包内默认 XML，避免 `ros2` 因无效 URI 无法创建节点。

## 5. 验证环境与锁文件

新的登录 Bash 和新开启的交互式 Bash 都会自动加载 profile；carrier 的 `postinst` 会在
`/etc/bash.bashrc` 写入一个受标记管理的 source 片段。已打开的终端不会被自动修改，
可重新打开 Bash，或手工加载：

```bash
source /etc/profile.d/zj_humanoid.sh
printf 'device=%s distro=%s robot=%s configured=%s compose=%s\n' \
  "$ZJ_DEVICE" "$ZJ_ROS_DISTRO" "$ROBOT_TYPE" \
  "$ZJ_ROBOT_TYPE_CONFIGURED" "${COMPOSE_PROFILES:-}"
```

检查锁文件：

```bash
cat /usr/lib/navi-common-dep/manifest.lock.json
cat /usr/lib/navi-pico-common-dep/manifest.lock.json
```

`zj_humanoid.sh` 只加载 ROS 2：Pico 使用 Humble，Orin 22.04 使用 Humble，Orin
24.04 使用 Jazzy。它不设置 ROS 1 Master、`ROS_IP` 或 `ROS_HOSTNAME`。

## 6. 给其他团队交付

交付物应包含：

```text
目标 common deb
对应 manifest.lock.json（已嵌入 deb）
本操作文档
目标、机型和 ROS 2 兼容性说明
```

不要交付包含现场机型或账号信息的已编辑 `device.env`。由接收方在自己的设备上通过
`configure` 命令生成该文件；这样同一个 common deb 可以安全部署到多个机器人。
