# Middleware 部署文档

本文说明当前 Middleware 总包的构建、预检、安装、服务检查和故障回滚方法。

## 1. 构建产物

构建输入位于：

```text
system_deployment/release/version.json
system_deployment/release/package-urls.json
system_deployment/release/supervisor.json
```

构建命令：

```bash
./system_deployment/build/build_release.sh --dry-run
./system_deployment/build/build_release.sh
```

当前输出：

```text
dist/Middleware-2.0.0-1.run
```

`--dry-run` 只校验配置并列出下载项，不下载模块，也不安装设备。

## 2. 安装前检查

将 `.run` 包复制到目标设备后执行：

```bash
chmod +x Middleware-2.0.0-1.run
sudo ./Middleware-2.0.0-1.run -- --pretest
```

预检重点检查：

- 目标 Ubuntu/架构是否匹配。
- common DEB 和模块下载内容是否完整。
- 设备是否能识别 target。
- 机型参数是否明确。
- 已有 `ROBOT_TYPE` 与本次安装参数是否冲突。

## 3. 安装命令

安装时必须指定目标机型：

```bash
sudo ./Middleware-2.0.0-1.run -- --robot-type WA1
```

PICO 设备如果要修改已有机型身份，需要显式确认：

```bash
sudo ./Middleware-2.0.0-1.run -- \
  --robot-type I2 \
  --confirm-robot-type-change
```

安装器会根据设备系统和 target 选择 ORIN/PICO/RDK 方案，执行：

1. 校验 release manifest 和 payload SHA256。
2. 安装 common DEB 和模块 DEB/RUN。
3. 复制随包配置文件。
4. 生成 Middleware 环境文件。
5. 写入硬件配置。
6. 安装或注册 Supervisor 服务。
7. 更新安装状态和当前版本信息。

## 4. 主要部署位置

ORIN 常用路径：

```text
/home/naviai/navi_project/
/etc/naviai/
/etc/naviai/supervisor-agent/
/etc/zj_humanoid/
/var/opt/hardware_body.yaml
```

PICO 常用路径：

```text
/home/nav01/zj_humanoid/
/etc/nav01/
/etc/zj_humanoid/
/var/opt/hardware_body.yaml
```

公共版本和状态信息由安装器及 release-state 逻辑维护，不要直接修改生成的 `/run` 文件。

## 5. 环境变量和配置

安装后环境通常由 Middleware 模板生成，并写入设备对应的环境目录。常见变量包括：

```text
ROS_DOMAIN_ID
RMW_IMPLEMENTATION
ROS_LOCALHOST_ONLY
CYCLONEDDS_URI
ROBOT_TYPE
ZJ_DEVICE
```

检查当前环境：

```bash
source /etc/profile.d/zj_humanoid.sh 2>/dev/null || true
env | grep -E '^(ROS_|RMW_|CYCLONEDDS_URI|ROBOT_TYPE|ZJ_)'
```

硬件默认配置来自 `config/config_map.yaml` 对应的型号文件，并写入：

```text
/var/opt/hardware_body.yaml
```

升级时安装器应保留用户已有字段，并合并当前型号默认值；不要直接覆盖用户配置。

### 5.1 WA-T 网络与配置文件位置

当前 WA-T 网络约定如下：

| 设备或部件 | 地址 | 由什么配置决定 |
| --- | --- | --- |
| Orin 主机 | `192.168.218.100/24` | 现场已有网卡地址 |
| Pico 主机 | `192.168.218.66/24` | 现场已有网卡地址 |
| Livox 雷达 | `192.168.218.17` | Orin 的 `LIVOX_LIDAR_IP` |
| ROS 2 网段选择 | `192.168.218.0/24` | `/etc/zj_humanoid/cyclonedds.xml` |

CycloneDDS XML 只负责从已有网卡中选择通信接口，不能给网卡分配地址。现场网卡已经使用
`192.168.218.100/24`（Orin）和 `192.168.218.66/24`（Pico）时，ROS 2 启动会按下面的 XML 选择
`192.168.218.0/24` 网段；这次仓库变更不包含网卡管理。

```bash
ip -brief address
ip route
```

配置链和设备上的最终位置如下：

| 作用 | 仓库源文件 | 设备最终位置 |
| --- | --- | --- |
| 设备身份、机型、ROS Domain | `common/deploy_common.py`、`common/templates/device.env.example` | `/etc/zj_humanoid/device.env` |
| 公共 Shell 环境 | `common/files/etc/profile.d/zj_humanoid.sh` | `/etc/profile.d/zj_humanoid.sh` |
| Orin 公共环境 | `common/templates/Middleware.orin.env` | `/etc/naviai/Middleware.env` |
| Pico 公共环境 | `common/templates/Middleware.pico.env` | `/etc/nav01/Middleware.env` |
| CycloneDDS 接口选择 | `common/files/etc/zj_humanoid/cyclonedds.xml` | `/etc/zj_humanoid/cyclonedds.xml` |
| Orin 导航与 Livox 地址 | `assets/orin-humble/environment-defaults/orin-humble/navigation.env` | `/etc/naviai/navigation/navigation.env` |
| Livox 自动识别结果 | 运行时生成 | `/etc/naviai/navigation/lidar.auto.env` |
| Agent 基础配置 | `packages/supervisor-agent/resources/{orin,pico}-modules.json` | `/etc/naviai/supervisor-agent/modules.json` 或 `/etc/nav01/supervisor-agent/modules.json` |
| 模块 RPC 注册 | `release/supervisor.json` 或 WA-T 特殊 Supervisor 配置 | 对应 Agent 的 `modules.d/*.json` |

环境加载顺序是：`/etc/profile.d/zj_humanoid.sh` 读取并校验 `device.env`，随后
`Middleware.env` 加载目标 ROS、DDS 和导航环境，最后由各模块的 Supervisor 启动脚本加载它。
常用基础值为 `ROS_DOMAIN_ID=72`、`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`、
`ROS_LOCALHOST_ONLY=0` 和 `CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml`。
`CYCLONEDDS_URI` 的值通常不需要改；换网段时修改 XML 的 `NetworkInterface` 和操作系统网卡地址即可。

在已经安装的 Orin 上，确认这条链路：

```bash
source /etc/naviai/Middleware.env
printf 'ROBOT_TYPE=%s\nROS_DOMAIN_ID=%s\nRMW_IMPLEMENTATION=%s\nROS_LOCALHOST_ONLY=%s\nCYCLONEDDS_URI=%s\nLIVOX_LIDAR_IP=%s\n' \
  "$ROBOT_TYPE" "$ROS_DOMAIN_ID" "$RMW_IMPLEMENTATION" "$ROS_LOCALHOST_ONLY" \
  "$CYCLONEDDS_URI" "${LIVOX_LIDAR_IP:-}"
grep -n 'NetworkInterface' /etc/zj_humanoid/cyclonedds.xml
```

Pico 将第一行替换为 `source /etc/nav01/Middleware.env`。修改仓库源配置后必须重新构建并安装总包；
设备上的 XML 会由 system-config 重新部署，导航目录则按 `overwrite=false` 保留已有的
`navigation.env`，所以已有设备若要切换 Livox 地址，需要确认该文件中的 `LIVOX_LIDAR_IP` 已更新。

### 5.2 Supervisor 监控地址

Supervisor 监控链路包含三类地址，不能只改其中一处：

1. `release/supervisor.json`（或 WA-T 专用的 `release/special-wa-t-jk2-v1-supervisor.json`）中，
   `targets.<target>.supervisor.internal_ip` 是本机各模块 XML-RPC 的绑定地址。Orin 为
   `192.168.218.100`，Pico 为 `192.168.218.66`。
2. `packages/supervisor-agent/resources/orin-modules.json` 中的 `remote_agents.pico.endpoint`
   必须指向 `http://192.168.218.66:9080`；Pico 的 `pico-modules.json` 中本机模块 endpoint 也使用
   `192.168.218.66`。
3. `supervisor_modules` 的端口和模块注册由构建器生成到设备的
   `/etc/naviai/supervisor-agent/modules.d/*.json` 或 `/etc/nav01/supervisor-agent/modules.d/*.json`。

修改仓库配置后必须重新构建并安装总包，安装器会重新生成：

```text
/etc/naviai/supervisor-agent/modules.json
/etc/nav01/supervisor-agent/modules.json
/etc/naviai/supervised-stack/<module>/supervisor-entrypoint.sh
/run/naviai/<module>/supervisord.conf
```

不要把 `/run/naviai/<module>/supervisord.conf` 当作永久配置直接修改；它会在服务重启时重新生成。
升级后可用下面的只读检查确认旧地址是否仍残留：

```bash
grep -RInE '192\.168\.217\.(66|100)|192\.168\.218\.' \
  /etc/naviai/supervisor-agent /etc/nav01/supervisor-agent /etc/naviai/supervised-stack /run/naviai 2>/dev/null
systemctl restart zj-humanoid-orin-supervisor-agent.service  # Orin
systemctl restart zj-humanoid-pico-supervisor-agent.service  # Pico
```

Orin Agent 还会通过 `remote_agents.pico.endpoint` 访问 Pico Agent；如果 Orin 本机模块正常但页面只有
Orin，没有 `pico/*`，优先检查 Orin 的 `modules.json`、Pico 的 `:9080` 服务和两台设备之间的 218 网段连通性。

## 6. 服务和聚合 Agent 检查

查看 Supervisor Agent：

```bash
systemctl status zj-humanoid-orin-supervisor-agent.service
systemctl status zj-humanoid-pico-supervisor-agent.service
```

查看模块注册：

```bash
find /etc/naviai/supervisor-agent/modules.d -maxdepth 1 -type f -print
cat /etc/naviai/supervisor-agent/modules.json
```

查看托管模块：

```bash
systemctl list-units 'zj-humanoid-*-supervisor.service'
```

查看服务日志：

```bash
journalctl -u zj-humanoid-orin-supervisor-agent.service -b
journalctl -u zj-humanoid-orin-<module>-supervisor.service -b
```

聚合页面或 RPC 中应能看到模块的注册状态、端口、服务状态和最近错误。永久修改回到 `release/supervisor.json` 后重新构建安装。

## 7. ORIN 和 PICO 启动方式

ORIN 主要使用宿主机 Docker Compose 或 Supervisor：

```bash
docker compose -f /home/naviai/navi_project/docker-compose.yaml ps
```

PICO 主要使用 systemd：

```bash
sudo systemctl daemon-reload
systemctl status zj-humanoid-*-supervisor.service
```

## 8. 版本和安装状态

查看版本工具（如果目标包已安装）：

```bash
navi-version
navi-version --json
```

安装失败时，先保留安装日志和状态文件。不要直接删除旧版本目录；确认新版本安装失败后，由安装器的回滚/重装流程恢复旧版本。

## 9. 故障排查顺序

1. 运行 `.run -- --pretest`。
2. 确认 `ROBOT_TYPE` 和 target。
3. 检查 `/var/opt/hardware_body.yaml`。
4. 检查 Middleware 环境变量和 DDS 配置。
5. 检查 Agent 的 `modules.json` 和 `modules.d/`。
6. 检查 systemd、Docker Compose 和 journal 日志。
7. 对照安装状态和 release manifest，确认是否为模块下载、安装或启动阶段失败。
