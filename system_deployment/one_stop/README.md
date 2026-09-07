# 唯一总安装包入口

本目录是唯一面向使用者的部署配置与构建入口：只编辑 `package-urls.json`，只运行 `build_release.sh`。不要使用或创建单独平台、单独模块的打包清单。

```bash
./system_deployment/one_stop/build_release.sh
```

产物固定生成到仓库根目录的 `dist/`：
`dist/navi_one_stop_installer-<version>.run`。

使用 `--dry-run` 可仅检查目标、URL 和配置结构，不下载任何包：

```bash
./system_deployment/one_stop/build_release.sh --dry-run
```

```bash
./navi_one_stop_installer-<version>.run -- --list-targets
./navi_one_stop_installer-<version>.run -- --info
./navi_one_stop_installer-<version>.run --pretest
sudo ./navi_one_stop_installer-<version>.run -- --robot-type WA1
```

`--pretest` 是只读检查：自动识别 target，列出总包内 DEB 和各子运行包的期望版本、当前已装版本及
预计动作（`install`、`upgrade`、`reinstall` 或 `downgrade blocked`），不会停止服务或改动系统。

## 安装顺序

```text
1. 自动识别 OS、OS 版本和 CPU 架构（或使用 --target 指定）
2. 内嵌 system-config：写入 profile、Middleware、CycloneDDS 和设备身份
3. extra_debs：按 package-urls.json 的顺序 dpkg -i 并执行安装器
4. runs：按 package-urls.json 的顺序执行；仅明确声明支持该参数的包接收 `--robot-type`
5. supervisor：安装由同一配置定义的模块注册、`supervisord` 启动脚本和 systemd 服务
```

```text
Orin Humble：chassis → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio → vision
Pico Humble：robot → upperlimb
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```

## Supervisor 服务

不要再维护独立的 `supervised_stack` manifest。`package-urls.json` 的 target 内：

- `supervisor` 是 Supervisor Agent 的 RPC、密码、运行目录和模块目录约定；
- `supervisor_modules` 定义模块端点和受管服务；`external` 仅注册既有服务的 RPC，`managed` 会生成
  `supervisord`、对应 systemd 单元及 Agent 的模块 JSON。

当前 Orin Humble 注册 chassis、sensor、robot、audio、vision：其中 robot 与 audio 使用各自的
`navi-orin-*-supervisor.service`，并保留 RPC 端口 `19002`、`19003`。Pico 的独立上肢服务不在
本次清理中擅自重新启用，避免再次与下肢包里的 RTIPC 进程冲突；待其启动边界确认后，只需在同一
`pico-humble` target 的 `supervisor_modules` 中增加配置。

当 target 声明 `supervisor` 时，总包会同时内嵌并安装对应的 Supervisor Agent；不再依赖另一份
Agent `.run` 包。Audio 的 `start_policy: "supervisor"` 只会抑制厂商 `.run` 最后一条前台 ROS
启动命令，依赖安装内容和其余安装逻辑保持原样，随后由 `navi-orin-audio-supervisor.service` 启动。

## Vision 服务

Orin Humble 与 Jazzy 都会在 Vision `.run` 安装成功后部署并启用
`navi-vision-supervisor.service`。它使用文档要求的独立运行环境：
`ROS_DOMAIN_ID=72`、`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`、
`ROS_LOCALHOST_ONLY=0`，并明确取消 `CYCLONEDDS_URI`。服务启动命令为：

```bash
ros2 launch navi_vision_pkg face_detection_node.launch.py \
  selected_camera:=auto camera_auto_timeout_sec:=8.0
```

Vision 只消费 Sensor 已发布的图像；相机不可用时服务会保持运行并按安装包的自动选择逻辑等待。
Vision `.run` 已内嵌其 `*_dep.deb`、模型与运行时；总包不会再单独安装 Vision 依赖 DEB，避免
厂商安装器把重复预装状态判定为不安全的 legacy takeover。

各 target 的公共运行依赖应由对应母盘提供；总包不再下载或安装 common carrier。母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。

聚合界面的日志显示在对应进程下的独立面板中，服务状态刷新不会关闭面板；可手动刷新或关闭日志。
受管模块使用非登录 shell 执行命令，以保留启动脚本设置的虚拟环境和 ROS 日志路径。
Agent 密码初始化后会重启已安装的 chassis 服务（包括当前未运行的服务），使其重新生成 RPC 监听配置。

Chassis 使用原 `navi-orin-chassis.service` 接入 Supervisor（19004），替换厂商直接启动 ROS 的方式，避免并行启动重复节点。
