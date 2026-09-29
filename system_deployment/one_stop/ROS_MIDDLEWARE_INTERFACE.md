# ROS 2 中间件接口说明

本文面向需要在机器人上访问 ROS 2 话题、服务和动作的外部模块。

## 统一通信参数

| 参数 | Pico | Orin |
|---|---|---|
| ROS 发行版 | Humble | Humble |
| `ROS_DOMAIN_ID` | `72` | `72` |
| `RMW_IMPLEMENTATION` | `rmw_cyclonedds_cpp` | `rmw_cyclonedds_cpp` |
| `ROS_LOCALHOST_ONLY` | `0` | `0` |
| DDS 配置 | `/etc/zj_humanoid/cyclonedds.xml` | `/etc/zj_humanoid/cyclonedds.xml` |

同一机器人上的模块必须使用相同的 Domain ID 和 RMW 实现，才能发现彼此的话题和服务。

## 加载环境

Pico：

```bash
source /etc/nav01/Middleware.env
```

Orin：

```bash
source /etc/naviai/Middleware.env
```

外部模块启动脚本建议：

```bash
#!/usr/bin/env bash
set -euo pipefail

source /etc/nav01/Middleware.env        # Pico
# source /etc/naviai/Middleware.env     # Orin

ros2 topic list
```

如果脚本使用 `set -u`，必须先加载 Middleware.env，再加载自己的 ROS 工作空间。

## 查看当前环境

```bash
echo "ROS_DISTRO=$ROS_DISTRO"
echo "ROS_DOMAIN_ID=$ROS_DOMAIN_ID"
echo "RMW_IMPLEMENTATION=$RMW_IMPLEMENTATION"
echo "ROS_LOCALHOST_ONLY=$ROS_LOCALHOST_ONLY"
echo "CYCLONEDDS_URI=$CYCLONEDDS_URI"
echo "MIDDLEWARE_ENV_FILE=$MIDDLEWARE_ENV_FILE"
```

检查结果应为：

```text
ROS_DISTRO=humble
ROS_DOMAIN_ID=72
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ROS_LOCALHOST_ONLY=0
CYCLONEDDS_URI=file:///etc/zj_humanoid/cyclonedds.xml
```

检查当前 ROS 2 CLI：

```bash
ros2 doctor --report
ros2 topic list
ros2 service list
ros2 action list
```

## 访问话题

查看话题类型：

```bash
ros2 topic type /topic_name
```

查看发布节点和 QoS：

```bash
ros2 topic info -v /topic_name
```

查看消息：

```bash
ros2 topic echo /topic_name
```

查看频率：

```bash
ros2 topic hz /topic_name
```

例如读取联合状态：

```bash
ros2 topic echo /zj_humanoid/robot/joint_states
```

相机图像示例：

```bash
ros2 topic list | grep -E 'camera|image|depth'
ros2 topic hz /zj_humanoid/sensor/left_wrist/color/image_raw
ros2 topic hz /zj_humanoid/sensor/left_wrist/depth_registered/image_raw
```

## 调用服务

先确认服务类型：

```bash
ros2 service type /service_name
```

再按照返回的完整类型调用。例如：

```bash
ros2 service call /service_name package_name/srv/ServiceType '{}'
```

不能根据服务名称猜测类型；如果类型错误，会出现 `The passed type is not a service`。

## 使用不同 ROS 工作空间

加载 Middleware.env 后，再加载自己的工作空间：

```bash
source /etc/nav01/Middleware.env
source /opt/ros/humble/setup.bash
source /path/to/your_workspace/install/setup.bash
```

如果自己的工作空间带有不同 ROS 发行版，不能与 Humble 混用。发现 `noetic`、`foxy` 或其他发行版路径时，重新打开终端并只加载目标环境。

## 发现不到话题时检查

```bash
printenv | grep -E 'ROS_|RMW_|CYCLONEDDS'
ros2 node list
ros2 topic list
```

重点确认：

1. `ROS_DOMAIN_ID` 两端都是 `72`。
2. `RMW_IMPLEMENTATION` 两端都是 `rmw_cyclonedds_cpp`。
3. `ROS_LOCALHOST_ONLY=0`。
4. `CYCLONEDDS_URI` 指向存在的文件。
5. 网卡和防火墙允许 DDS 通信。
6. 没有混入 `/opt/ros/noetic` 或其他发行版路径。

Pico 的 Middleware.env 已设置：

```bash
export ROS2CLI_DISABLE_DAEMON=1
```

因此不需要依赖旧的 ROS 2 daemon 缓存；重新加载环境后直接执行 `ros2 topic list` 即可。

## 聚合界面与模块状态接口

每台设备的 Supervisor Agent 提供 HTTP 接口，默认端口为 `9080`。

Pico：

```text
http://<PICO_IP>:9080
```

WA-T Pico 默认地址：

```text
http://192.168.217.66:9080
```

Orin：

```text
http://<ORIN_IP>:9080
```

查询设备和模块状态：

```bash
curl -s http://<DEVICE_IP>:9080/api/v1/modules
```

返回结果示例：

```json
{
  "device": "pico",
  "modules": [
    {
      "name": "robot",
      "id": "robot",
      "device": "pico",
      "reachable": true,
      "processes": [
        {
          "name": "robot",
          "state": "RUNNING",
          "pid": 1234,
          "exit_status": 0,
          "spawn_error": ""
        }
      ]
    }
  ]
}
```

`reachable` 表示聚合端能否连接模块 Supervisor RPC；`state` 表示进程状态。常见状态包括：

```text
RUNNING
STOPPED
STARTING
EXITED
FATAL
```

WA-T Pico 模块及 RPC 端口：

| 模块 | RPC 地址 |
|---|---|
| robot | `192.168.217.66:19002/RPC2` |
| upperlimb | `192.168.217.66:19003/RPC2` |
| hand | `192.168.217.66:19005/RPC2` |

检查端口：

```bash
ss -ltnp | grep -E ':19002|:19003|:19005'
```

检查某个模块的 Supervisor RPC 是否可达：

```bash
curl -i -u agent:1 \
  http://192.168.217.66:19002/RPC2
```

HTTP `400 Bad Request` 通常表示 RPC 端点已经连接成功，但请求不是 XML-RPC 格式；`Connection refused` 才表示服务没有监听或端口不可达。

查看 Agent 服务：

```bash
sudo systemctl status zj-humanoid-pico-supervisor-agent.service --no-pager
```

Orin 使用：

```bash
sudo systemctl status zj-humanoid-orin-supervisor-agent.service --no-pager
curl -s http://<ORIN_IP>:9080/api/v1/modules
```

外部聚合程序只需要访问 Agent 的 `9080` 接口，不需要直接连接每个模块的 `19002`、`19003` 或 `19005` 端口。
