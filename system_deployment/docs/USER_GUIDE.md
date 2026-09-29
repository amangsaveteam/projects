# Navi One-Stop 安装与运行手册

本文面向设备使用者，说明如何安装、查看和控制 One-Stop 管理的服务。部署人员请使用 [README.md](README.md)。

## 1. 适用设备

| 设备 | 系统要求 | 主要服务 |
| --- | --- | --- |
| Pico Humble | Ubuntu 20.04 amd64 | robot、upperlimb、display、Pico Supervisor Agent |
| Orin Humble | Ubuntu 22.04 arm64 | sensor、robot、audio、vision、导航及其他 Orin 模块 |

Pico 与 Orin 是不同设备。两台设备可使用相同端口号；不要把 Pico 的服务地址替换为 Orin 地址，反之亦然。

## 2. 安装前检查

在目标设备执行以下命令：

```bash
uname -m
. /etc/os-release
printf 'VERSION_ID=%s\n' "$VERSION_ID"
```

Pico 必须是 Ubuntu 20.04、`x86_64`；Orin Humble 必须是 Ubuntu 22.04、`aarch64`。

Pico 安装前还必须确认设备机型。WA 系列设备必须使用实际 WA 型号，例如：

```bash
sudo cat /etc/zj_humanoid/device.env
```

期望示例：

```text
ZJ_DEVICE=PICO
ROBOT_TYPE=WA1-S
ROS_DOMAIN_ID=72
COMPOSE_PROFILES=wa1
```

不要把 WA 设备按 I2 安装，也不要手工把 `COMPOSE_PROFILES` 改为 `rx`。

## 3. 安装总包

将 `.run` 安装包复制到目标设备后，先查看支持的 target 和安装内容：

```bash
chmod +x navi_one_stop_installer-<version>.run
./navi_one_stop_installer-<version>.run -- --list-targets
./navi_one_stop_installer-<version>.run -- --info
./navi_one_stop_installer-<version>.run --pretest
```

Pico 必须显式传入正确机型：

```bash
sudo ./navi_one_stop_installer-<version>.run -- --target pico-humble --robot-type WA1-S
```

Orin 示例：

```bash
sudo ./navi_one_stop_installer-<version>.run -- --target orin-humble --robot-type WA2_LS
```

安装期间不要断电、重启或手工启动/停止受管服务。若任一模块安装失败，总包会保留受管服务停止状态，修复失败原因后重新执行完整安装。

## 4. Pico 服务架构

Pico 的本机 Agent 地址是：

```text
http://127.0.0.1:9080
```

Orin 聚合页面会通过 Pico Agent 展示 `pico / robot`、`pico / upperlimb` 和 `pico / display`。

| Pico 模块 | Supervisor RPC | systemd 服务 |
| --- | --- | --- |
| robot | `192.168.217.66:19002/RPC2` | `zj-humanoid-pico-robot-supervisor.service` |
| upperlimb | `192.168.217.66:19003/RPC2` | `zj-humanoid-pico-upperlimb-supervisor.service` |
| display | `192.168.217.66:19004/RPC2` | `zj-humanoid-pico-display-supervisor.service` |
| Pico Agent | `192.168.217.66:9080` | `zj-humanoid-pico-supervisor-agent.service` |

WA 设备不使用下肢模块。`navi-pico-legged-supervisor.service` 必须处于禁用状态，避免占用 display 的 `19004` 端口。

## 5. 查看聚合状态和日志

在 Pico 上查看本机模块状态：

```bash
curl -sS http://127.0.0.1:9080/api/v1/modules
```

在浏览器访问 Pico Agent 页面：

```text
http://<Pico-IP>:9080
```

状态含义：

| 状态 | 含义 |
| --- | --- |
| `RUNNING` | Supervisor 管理的进程正在运行；仍应结合日志确认功能是否可用。 |
| `EXITED` | 进程已退出。 |
| `FATAL` / `BACKOFF` | 启动失败或多次重试失败。 |
| `reachable: false` | Agent 无法连接模块的 Supervisor RPC。 |

在页面中点击“日志尾部”查看服务日志。Pico upperlimb 使用本地日志文件：

```text
/var/log/naviai/upperlimb/upperlimb.log
```

Pico display 日志位于：

```text
/var/log/naviai/display/display.log
/var/log/naviai/display/ros/
```

## 6. Pico 日常控制

### 查看服务状态

```bash
sudo systemctl status --no-pager \
  zj-humanoid-pico-supervisor-agent.service \
  zj-humanoid-pico-robot-supervisor.service \
  zj-humanoid-pico-upperlimb-supervisor.service \
  zj-humanoid-pico-display-supervisor.service
```

### 启动、停止或重启服务

```bash
sudo systemctl restart zj-humanoid-pico-robot-supervisor.service
sudo systemctl restart zj-humanoid-pico-upperlimb-supervisor.service
sudo systemctl restart zj-humanoid-pico-display-supervisor.service
```

通常优先使用聚合页面的“启动 / 停止 / 重启”按钮。只有在服务本身未启动、端口未监听或排障时才使用 systemd 命令。

### 查看端口监听

```bash
sudo ss -ltnp '( sport = :19002 or sport = :19003 or sport = :19004 or sport = :9080 )'
```

期望：

- `19002`：robot Supervisor；
- `19003`：upperlimb Supervisor；
- `19004`：display Supervisor；
- `9080`：Pico Supervisor Agent。

## 7. Pico 启动顺序

上肢服务会等待 robot 发布以下状态后才启动上肢节点：

```yaml
state: 5
state_info: STATE_ROBOT_RUN
```

可在 Pico 上验证：

```bash
source /etc/nav01/Middleware.env
ros2 topic echo --once /zj_humanoid/robot/robot_state
```

若 robot 未到达 `STATE_ROBOT_RUN`，upperlimb 会保持等待，不应强行反复重启。先检查 robot：

```bash
sudo journalctl -u zj-humanoid-pico-robot-supervisor.service -n 200 --no-pager
```

## 8. 常见问题

### 8.1 页面显示 upperlimb `Connection refused`

检查上肢 Supervisor 是否运行以及 `19003` 是否监听：

```bash
sudo systemctl status --no-pager zj-humanoid-pico-upperlimb-supervisor.service
sudo ss -ltnp '( sport = :19003 )'
sudo journalctl -u zj-humanoid-pico-upperlimb-supervisor.service -n 200 --no-pager
```

如果上肢在等待 robot 状态，先使 robot 到达 `STATE_ROBOT_RUN`。

### 8.2 display 显示 `401 Unauthorized` 或无法启动

这通常表示旧下肢服务占用了 `19004`。在 WA 设备执行：

```bash
sudo systemctl disable --now navi-pico-legged-supervisor.service
sudo rm -f /etc/nav01/supervisor-agent/modules.d/legged.json
sudo systemctl restart zj-humanoid-pico-display-supervisor.service
```

再确认端口归属：

```bash
sudo ss -ltnp '( sport = :19004 )'
```

### 8.3 display 为 `RUNNING`，但画面未显示

`RUNNING` 只表示 `ros2 launch` 父进程存在。检查 display 日志中 `video_player` 是否启动失败：

```bash
sudo tail -n 200 /var/log/naviai/display/display.log
sudo journalctl -u zj-humanoid-pico-display-supervisor.service -n 200 --no-pager
```

如出现 `Failed to get logging directory`，确认新版总包已部署，并检查：

```bash
sudo install -d -m 0755 \
  /var/lib/navi-display \
  /var/lib/navi-display/ros \
  /var/log/naviai/display/ros
sudo systemctl restart zj-humanoid-pico-display-supervisor.service
```

### 8.4 upperlimb 或 display 不断重启

先读取对应日志，不要持续手动重启：

```bash
sudo journalctl -u zj-humanoid-pico-upperlimb-supervisor.service -n 200 --no-pager
sudo journalctl -u zj-humanoid-pico-display-supervisor.service -n 200 --no-pager
sudo tail -n 200 /var/log/naviai/upperlimb/upperlimb.log
sudo tail -n 200 /var/log/naviai/display/display.log
```

上肢的 ROS 日志目录必须可用：

```bash
sudo install -d -o nav01 -g nav01 -m 0755 \
  /home/nav01/.ros \
  /var/log/naviai/upperlimb/ros
```

### 8.5 聚合页面日志读取失败

先检查 Pico Agent：

```bash
sudo systemctl status --no-pager zj-humanoid-pico-supervisor-agent.service
curl -sS http://127.0.0.1:9080/api/v1/modules
```

新版 Agent 对 upperlimb 直接读取本地日志文件，不依赖 Supervisor 的 XML-RPC 日志接口。旧 Agent 可能出现：

```text
Remote end closed connection without response
```

此时需部署包含新版 Agent 的总包。

## 9. 收集故障信息

需要提交问题时，请收集以下信息：

```bash
sudo cat /etc/zj_humanoid/device.env
sudo ss -ltnp '( sport = :19002 or sport = :19003 or sport = :19004 or sport = :9080 )'
curl -sS http://127.0.0.1:9080/api/v1/modules
sudo systemctl status --no-pager --full \
  zj-humanoid-pico-supervisor-agent.service \
  zj-humanoid-pico-robot-supervisor.service \
  zj-humanoid-pico-upperlimb-supervisor.service \
  zj-humanoid-pico-display-supervisor.service
sudo journalctl -u zj-humanoid-pico-upperlimb-supervisor.service -n 200 --no-pager
sudo journalctl -u zj-humanoid-pico-display-supervisor.service -n 200 --no-pager
```

注意不要提交 `/etc/nav01/supervisor-agent/supervisor-rpc.password` 的内容。
