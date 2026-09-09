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
3. extra_debs：从云端下载并按 package-urls.json 的顺序 dpkg -i、执行安装器
4. runs：按 package-urls.json 的顺序执行；仅明确声明支持该参数的包接收 `--robot-type`
5. supervisor：安装由同一配置定义的模块注册、`supervisord` 启动脚本和 systemd 服务
```

```text
Orin Humble：chassis → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio → vision
Pico Humble：Pico common → upperlimb common → robot → upperlimb
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```

每个 target 的安装脚本会在写入配置、停止服务或执行 `dpkg` 前再次校验当前 OS、版本和 CPU 架构。
因此 `pico-humble` 的 payload 只能在 Ubuntu 20.04 amd64 上安装；即使手工传入 `--target pico-humble`，
也不能在 Orin 的 Ubuntu 22.04 arm64 上执行。

## Supervisor 服务

不要再维护独立的 `supervised_stack` manifest。`package-urls.json` 的 target 内：

- `supervisor` 是 Supervisor Agent 的 RPC、密码、运行目录和模块目录约定；
- `supervisor_modules` 定义模块端点和受管服务；`external` 仅注册既有服务的 RPC，`managed` 会生成
  `supervisord`、对应 systemd 单元及 Agent 的模块 JSON。

Orin Humble 注册 chassis、sensor、robot、audio、vision。Orin 的 robot 由总包生成
`navi-orin-robot-supervisor.service` 并使用 `19002`；Sensor 已由自身安装包原生维护
Supervisor，故总包只注册并在共享凭据就绪后重启 `navi-sensor-host.service`，不会修改其配置文件。

Pico Humble 注册两个原生模块：PICO Robot 使用 `192.168.217.66:19002`，上肢使用
`192.168.217.66:19003`。两者的 Supervisor 配置和 systemd 服务分别由各自模块包维护；总包仅安装
PICO Agent、写入模块注册并在共享凭据就绪后重启服务。端口可以与 Orin 重复，因为绑定在不同设备。
Orin Agent 经由 PICO Agent（`192.168.217.66:9080`）汇总状态，界面显示为 `orin / robot` 与
`pico / robot`，而非跨设备直接代理 PICO 的 XML-RPC。

Common DEB 由目标匹配的独立构建机生成并发布：Pico Humble Common 在 Ubuntu 20.04 amd64 构建，
Orin Humble Common 在 Ubuntu 22.04 arm64 构建。总包不触发这些远程构建；它只从云端下载已发布的
Common DEB。Pico Common 是 PICO 的首个依赖 DEB，随后安装 upperlimb-common 与 robot。

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

Orin Humble 的 Sensor common DEB 依赖 `orin-common-deb`，但当前滚动发布的 Orin common 包仍使用旧包名
`navi-common-dep`。总包会按 `Orin common → 父依赖兼容包 → Sensor common → Sensor .run` 的顺序安装，
使 Sensor 的离线依赖校验器可用。其余 target 的公共运行依赖仍由对应母盘提供；母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。

聚合界面的日志显示在对应进程下的独立面板中，服务状态刷新不会关闭面板；可手动刷新或关闭日志。
受管模块使用非登录 shell 执行命令，以保留启动脚本设置的虚拟环境和 ROS 日志路径。
Agent 密码初始化后会重启已安装的 chassis 服务（包括当前未运行的服务），使其重新生成 RPC 监听配置。

Chassis 使用原 `navi-orin-chassis.service` 接入 Supervisor（19004），替换厂商直接启动 ROS 的方式，避免并行启动重复节点。
