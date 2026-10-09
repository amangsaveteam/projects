# WA-T 运行配置、日志与故障排查手册

本文面向现场维护人员。设备分为 Orin 和 Pico 两台主机；两台主机使用不同的配置目录，但 ROS 2 的 Domain 和 DDS 网段必须一致。

## 1. 先确认当前主机

```bash
hostname
ip -brief address
```

当前 WA-T 网络约定：

| 设备 | 地址 | 主要用户 | 公共环境 |
| --- | --- | --- | --- |
| Orin | `192.168.218.100/24` | `naviai` | `/etc/naviai/Middleware.env` |
| Pico | `192.168.218.66/24` | `nav01` | `/etc/nav01/Middleware.env` |
| Livox | `192.168.218.17` | — | Orin 的 `LIVOX_LIDAR_IP` |

ROS 2 网段切换只由 CycloneDDS 选择已有网卡，不会给网卡分配地址。网卡地址、路由和 DDS XML 是三件独立的配置。

## 2. 配置文件总表

### 2.1 两台主机都需要检查的文件

| 作用 | 设备路径 | 修改方式 |
| --- | --- | --- |
| 设备身份、机型、ROS Domain | `/etc/zj_humanoid/device.env` | `sudoedit`，修改后重新加载环境或重启相关服务 |
| 公共 Shell 环境 | `/etc/profile.d/zj_humanoid.sh` | 通常由安装包管理，不直接改逻辑 |
| CycloneDDS 默认配置 | `/etc/zj_humanoid/cyclonedds.xml` | 修改 `NetworkInterface` |
| 硬件机身配置 | `/var/opt/hardware_body.yaml` | 修改前备份；由安装器和上肢运行时使用 |
| Supervisor 运行目录 | `/run/naviai/<module>/supervisord.conf` | 只读检查，重启会重新生成，不作为永久配置修改 |
| Supervisor 模块日志 | `/var/log/naviai/<module>/` | 先查看，再决定是否重启 |

### 2.2 Orin 文件

| 作用 | 路径 |
| --- | --- |
| 公共环境 | `/etc/naviai/Middleware.env` |
| 导航手工配置 | `/etc/naviai/navigation/navigation.env` |
| Livox 自动识别结果 | `/etc/naviai/navigation/lidar.auto.env` |
| 机器人环境 | `/etc/naviai/robot/robot_env.sh` |
| Sensor Supervisor 配置 | `/etc/naviai/navi-sensor-host-supervisor.conf` |
| Supervisor Agent 总配置 | `/etc/naviai/supervisor-agent/modules.json` |
| Supervisor Agent 模块注册 | `/etc/naviai/supervisor-agent/modules.d/*.json` |
| Agent 密码 | `/etc/naviai/supervisor-agent/supervisor-rpc.password` |
| 受管模块启动脚本 | `/etc/naviai/supervised-stack/<module>/` |
| Orin 自定义 DDS 覆盖文件 | `/etc/naviai/cyclonedds-local.xml`（若 `CYCLONEDDS_URI` 指向它） |

### 2.3 Pico 文件

| 作用 | 路径 |
| --- | --- |
| 公共环境 | `/etc/nav01/Middleware.env` |
| 机器人启动目录 | `/etc/nav01/robot/` |
| 上肢启动目录 | `/etc/nav01/upperlimb/` |
| Supervisor Agent 总配置 | `/etc/nav01/supervisor-agent/modules.json` |
| Supervisor Agent 模块注册 | `/etc/nav01/supervisor-agent/modules.d/*.json` |
| Agent 密码 | `/etc/nav01/supervisor-agent/supervisor-rpc.password` |
| 受管模块启动脚本 | `/etc/nav01/supervised-stack/<module>/` |

夹爪 `gripper` 是独立 DEB 提供的模块，不等同于 `hand`（六维力）或 `hand-force`。
仓库中的 `supervisor.json` 只包含后两者；夹爪的 Supervisor 配置由 gripper DEB 安装到设备后维护。
消息包 `zj-humanoid-ros-humble-eyou-msgs_3.3.0+focal_all.deb` 只提供 `eyou_msgs` 类型，不能替代启动环境。

在 Pico 上定位夹爪包和启动配置：

```bash
dpkg -l | grep -Ei 'gripper|eyou'
find /opt/zj_humanoid /home/nav01 -name eyou_env.bash -o -iname '*gripper*' 2>/dev/null
grep -RInE 'gripper|eyou_interface|dual.launch|eyou_env' \
  /etc/nav01 /etc/systemd/system /run/naviai 2>/dev/null
```

## 3. 环境变量和生效顺序

先加载设备环境，再加载对应主机的 Middleware 环境：

```bash
# Orin
source /etc/naviai/Middleware.env

# Pico
source /etc/nav01/Middleware.env
```

查看当前生效值：

```bash
printf 'ROBOT_TYPE=%s\nROS_DOMAIN_ID=%s\nRMW_IMPLEMENTATION=%s\nROS_LOCALHOST_ONLY=%s\nCYCLONEDDS_URI=%s\n' \
  "${ROBOT_TYPE:-}" "${ROS_DOMAIN_ID:-}" "${RMW_IMPLEMENTATION:-}" \
  "${ROS_LOCALHOST_ONLY:-}" "${CYCLONEDDS_URI:-}"
```

通常应为：

```text
ROS_DOMAIN_ID=72
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ROS_LOCALHOST_ONLY=0
CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml
```

实际启动命令可能在模块脚本中重新设置这些变量，因此排障时必须检查进程环境：

```bash
pgrep -af 'ros2|realsense|uplimb|orin_robot'
sudo tr '\0' '\n' < /proc/<PID>/environ | grep -E 'ROS_|RMW_|CYCLONEDDS'
```

Pico Humble 的 `Middleware.env` 已直接集成 Eyou ROS 2 workspace 的环境初始化，等价于
`eyou_env.bash ros2` 中的 ROS 2 和 Eyou prefix 加载步骤。gripper Supervisor 只要通过标准环境链
加载 `/etc/nav01/Middleware.env` 就能找到 `eyou_msgs`，不再需要额外 source 一个环境文件。

`CYCLONEDDS_URI` 优先级高于默认 XML。Orin 如果显示：

```text
CYCLONEDDS_URI=file:///etc/naviai/cyclonedds-local.xml
```

就必须修改 `/etc/naviai/cyclonedds-local.xml`，修改 `/etc/zj_humanoid/cyclonedds.xml` 不会影响当前进程。

确认实际 DDS 文件：

```bash
DDS_FILE="${CYCLONEDDS_URI#file://}"
echo "$DDS_FILE"
grep -nE 'NetworkInterface|192\.168\.217|192\.168\.218' "$DDS_FILE"
```

## 4. 网络和 DDS 修改方法

先确认操作系统网卡：

```bash
ip -brief address
ip route
```

确认 DDS XML 选择 218 网段：

```bash
grep -n 'NetworkInterface' "$DDS_FILE"
```

期望看到：

```xml
<NetworkInterface address="192.168.218.0"/>
```

现场临时修改前先备份：

```bash
sudo cp -a "$DDS_FILE" "$DDS_FILE.bak.$(date +%Y%m%d-%H%M%S)"
sudo sed -i 's/192\.168\.217\.0/192.168.218.0/g' "$DDS_FILE"
```

修改后重启产生 ROS 2 节点的服务，再用无 daemon 查询：

```bash
ros2 topic list --no-daemon
```

如果仍出现 `does not match an available interface`，优先检查 `CYCLONEDDS_URI` 指向的文件，而不是只检查默认 XML。

## 5. Supervisor 配置、端口和日志

### 5.1 端口

| 服务 | 地址/端口 | 设备 |
| --- | --- | --- |
| Orin Sensor Supervisor | `192.168.218.100:19001` | Orin |
| Pico Robot Supervisor | `192.168.218.66:19002` | Pico |
| Pico Upperlimb Supervisor | `192.168.218.66:19003` | Pico |
| Pico Display Supervisor | `192.168.218.66:19004` | Pico |
| Orin/Pico Agent | `:9080` | 各自本机 |

检查端口：

```bash
sudo ss -ltnp | grep -E ':19001|:19002|:19003|:19004|:9080'
```

检查 Agent 聚合状态：

```bash
# Orin
curl -sS http://192.168.218.100:9080/api/v1/modules

# Pico
curl -sS http://192.168.218.66:9080/api/v1/modules
```

`reachable: false` 先检查对应 RPC 端口和 systemd 服务；`FATAL` 或 `EXITED` 再查看模块日志。

### 5.2 Supervisor 配置位置

永久配置来自仓库的 `release/supervisor.json` 或 WA-T 专用 Supervisor 配置，安装后生成到：

```text
/etc/naviai/supervisor-agent/modules.d/*.json
/etc/nav01/supervisor-agent/modules.d/*.json
/etc/naviai/supervised-stack/<module>/supervisor-entrypoint.sh
/run/naviai/<module>/supervisord.conf
```

不要直接把 `/run/naviai/<module>/supervisord.conf` 当作永久配置。要永久修改，应改仓库配置，重新构建并安装总包。

### 5.3 日志位置

Orin 常用日志：

```text
/var/log/naviai/audio/
/var/log/naviai/chassis/
/var/log/naviai/diagnosis-system/
/var/log/naviai/livox-lidar/
/var/log/naviai/naviai-nav2/
/var/log/naviai/naviai-nav2-rawdata/
/var/log/naviai/navigation/
/var/log/naviai/orbbec-camera/
/var/log/naviai/robot/
/var/log/naviai/vanjee-lidar/
/var/log/naviai/web-rviz/
/var/log/naviai/vision/
/var/log/navi-sensor-host/
```

Pico 常用日志：

```text
/var/log/naviai/robot/
/var/log/naviai/upperlimb/
/var/log/naviai/display/
/var/log/naviai/hand/
/var/log/naviai/hand-force/
```

日志查看：

```bash
tail -n 200 /var/log/naviai/upperlimb/upperlimb.log
tail -n 200 /var/log/naviai/upperlimb/ros/*.log
tail -n 200 /var/log/navi-sensor-host/realsense.out.log
tail -n 200 /var/log/navi-sensor-host/sensor_restart_service.err.log
```

Supervisor 主进程仍由 root 管理，日志目录按设备用户组开放读取。已有设备执行一次权限修复：

```bash
# Orin
sudo chgrp -R naviai /var/log/naviai
sudo find /var/log/naviai -type d -exec chmod g+rx,g+s {} +
sudo find /var/log/naviai -type f -exec chmod g+r {} +

# Pico
sudo chgrp -R nav01 /var/log/naviai
sudo find /var/log/naviai -type d -exec chmod g+rx,g+s {} +
sudo find /var/log/naviai -type f -exec chmod g+r {} +
```

## 6. 按故障现象排查

### 6.1 `ros2 topic list` 报 `rcl node's rmw handle is invalid`

```bash
source /etc/naviai/Middleware.env       # Orin
# source /etc/nav01/Middleware.env      # Pico
echo "$CYCLONEDDS_URI"
DDS_FILE="${CYCLONEDDS_URI#file://}"
grep -nE 'NetworkInterface|192\.168\.217|192\.168\.218' "$DDS_FILE"
ip -brief address
ros2 topic list --no-daemon
```

看到 `192.168.217.0` 时，修改实际 `DDS_FILE` 并重启对应服务。

### 6.2 RealSense 或 Sensor 模块退出

先区分 USB 问题和 DDS 问题：

```bash
lsusb | grep -i 'RealSense\|Intel'
rs-enumerate-devices
ls -l /dev/video*
sudo fuser -v /dev/video* 2>/dev/null || true
```

再看真正的子进程日志：

```bash
tail -n 200 /var/log/navi-sensor-host/realsense.out.log
tail -n 200 /var/log/navi-sensor-host/sensor_restart_service.err.log
```

如果出现 `does not match an available interface`，先修 DDS；如果出现 `No device detected`，再检查 USB、`/dev/video*` 和相机占用。

Sensor 原生 Supervisor RPC 监听检查：

```bash
sudo ss -ltnp | grep ':19001'
```

必要时重新配置 Orin Sensor RPC：

```bash
sudo python3 /etc/naviai/supervisor-agent/configure_native_rpc.py \
  /etc/naviai/navi-sensor-host-supervisor.conf \
  /etc/naviai/supervisor-agent/supervisor-rpc.password \
  192.168.218.100:19001
sudo systemctl restart zj-humanoid-sensor.service
```

### 6.3 Pico 上肢进程运行但没有话题

```bash
sudo systemctl status --no-pager zj-humanoid-pico-upperlimb-supervisor.service
tail -n 200 /var/log/naviai/upperlimb/upperlimb.log
source /etc/nav01/Middleware.env
ros2 node list --no-daemon
ros2 topic list --no-daemon
```

确认上肢进程使用的环境：

```bash
pgrep -af 'uplimb|upperlimb'
sudo tr '\0' '\n' < /proc/<PID>/environ | grep -E 'ROS_|RMW_|CYCLONEDDS'
```

Orin 侧还要确认机器人状态话题是否存在：

```bash
ros2 topic echo --once /zj_humanoid/robot/robot_state
```

### 6.4 Gripper 找不到 `eyou_msgs` 类型

交互式 shell 中能运行而 Supervisor 中失败，通常是因为 Supervisor 没有加载统一的
`/etc/nav01/Middleware.env`。夹爪启动命令应在同一个 Bash 进程中按以下顺序执行：

```bash
source /etc/nav01/Middleware.env
exec ros2 launch eyou_interface dual.launch.py
```

先验证消息类型和环境：

```bash
source /etc/nav01/Middleware.env
python3 -c 'from eyou_msgs.action import Execute; from eyou_msgs.msg import EyouState; print("eyou_msgs OK")'
```

永久修改应修改 gripper DEB 安装的启动脚本或 Supervisor 模板；不要只修改
`/run/naviai/gripper/supervisord.conf`，因为该文件会在服务重启时重新生成。修改后重启实际的
gripper Supervisor 服务，再检查：

```bash
sudo systemctl list-units --all --type=service | grep -Ei 'gripper|eyou'
ros2 topic list --no-daemon | grep -Ei 'gripper|hand|eyou'
```

### 6.5 Agent 页面看不到模块

```bash
sudo systemctl status --no-pager zj-humanoid-orin-supervisor-agent.service
sudo systemctl status --no-pager zj-humanoid-pico-supervisor-agent.service
curl -sS http://127.0.0.1:9080/api/v1/health
curl -sS http://127.0.0.1:9080/api/v1/modules
```

检查注册文件中的 endpoint：

```bash
grep -RInE 'endpoint|192\.168\.217|192\.168\.218' \
  /etc/naviai/supervisor-agent /etc/nav01/supervisor-agent 2>/dev/null
```

Orin 汇总 Pico 时，Orin 的 `modules.json` 必须指向：

```text
http://192.168.218.66:9080
```

## 7. 修改原则

1. 先确认当前生效文件：查看 `CYCLONEDDS_URI`、`ROS_DOMAIN_ID` 和进程环境。
2. 修改前备份：`sudo cp -a file file.bak.<timestamp>`。
3. 修改 `/etc` 下的配置后，只重启拥有该配置的服务。
4. 不直接修改 `/run/naviai` 下的生成文件。
5. 修改仓库源配置后，重新执行构建检查并安装总包；设备运行时文件不会自动从仓库刷新。
6. 出现 ROS 2 错误时优先使用 `--no-daemon`，避免旧的 ros2 daemon 缓存干扰判断。

## 8. 仓库源文件对应关系

| 设备运行文件 | 仓库源文件 |
| --- | --- |
| `/etc/zj_humanoid/cyclonedds.xml` | `system_deployment/common/files/etc/zj_humanoid/cyclonedds.xml` |
| `/etc/naviai/Middleware.env` | `system_deployment/common/templates/Middleware.orin.env` |
| `/etc/nav01/Middleware.env` | `system_deployment/common/templates/Middleware.pico.env` |
| `/etc/naviai/navigation/navigation.env` | `system_deployment/assets/orin-humble/environment-defaults/orin-humble/navigation.env` |
| `/etc/naviai/supervisor-agent/modules.json` | `system_deployment/packages/supervisor-agent/resources/orin-modules.json` |
| `/etc/nav01/supervisor-agent/modules.json` | `system_deployment/packages/supervisor-agent/resources/pico-modules.json` |
| Supervisor 模块配置 | `system_deployment/release/supervisor.json` |
| WA-T 特殊 Supervisor 模块配置 | `system_deployment/release/special-wa-t-jk2-v1-supervisor.json` |

仓库修改完成后执行：

```bash
python3 system_deployment/architecture/check_architecture.py
bash system_deployment/build/build_release.sh --dry-run
```
