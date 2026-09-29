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
CYCLONEDDS_URI
ROS_IP
ROS_MASTER_URI
ROS_HOSTNAME
ROBOT_TYPE
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
