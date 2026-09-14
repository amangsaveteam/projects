# 唯一总安装包入口

本目录是唯一面向使用者的部署配置与构建入口：分别编辑安装配置 `package-urls.json` 和运行配置 `supervisor.json`，只运行 `build_release.sh`。不要使用或创建单独平台、单独模块的打包清单。

```bash
./system_deployment/one_stop/build_release.sh
```

产物固定生成到仓库根目录的 `dist/`：
`dist/navi_one_stop_installer-<version>.run`。

构建使用独立临时目录，打包失败时清理未完成输出并保留上一份正式产物。生成的 `.run` 在正常结束、
安装失败或收到可处理的退出信号后清理本次解压目录；断电、SIGKILL 等无法运行清理逻辑的情况除外。
历史遗留目录不会自动批量删除，以免影响其他正在执行的安装任务。

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
3. extra_debs：使用构建阶段已下载并内嵌的工件，按 package-urls.json 的顺序 dpkg -i、执行安装器
4. runs：按 package-urls.json 的顺序执行；仅明确声明支持该参数的包接收 `--robot-type`
5. supervisor：安装由 supervisor.json 定义的模块注册、`supervisord` 启动脚本和 systemd 服务
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

Orin Humble 在执行模块安装器前创建 Robot 的 `/var/lib/navi` 工作目录及日志目录，避免厂商服务
在执行 `ExecStartPre` 前因 `WorkingDirectory` 不存在而报 `200/CHDIR`。

## Supervisor 服务

不要再维护独立的 `supervised_stack` manifest。`supervisor.json` 的 target 内：

- `supervisor` 是 Supervisor Agent 的 RPC、密码、运行目录和模块目录约定；
- `supervisor_modules` 定义模块端点和受管服务；`external` 仅注册既有服务的 RPC，`managed` 会生成
  `supervisord`、对应 systemd 单元及 Agent 的模块 JSON。

Orin Humble 注册 chassis、sensor、robot、audio、vision。Orin 的 robot 由总包生成
`navi-orin-robot-supervisor.service` 并使用 `19002`；Sensor 已由自身安装包原生维护
Supervisor。总包在共享凭据就绪后，备份原配置并补齐 Sensor 的认证 TCP RPC（仅监听内网 19001），
保留原生相机程序配置，再重启 `navi-sensor-host.service`；重复安装也会重新应用该配置。

`external` 模块声明的 `restart_service` 也参与统一停服和恢复。服务恢复由 systemd 解析单元位置，
支持 DEB 安装到 `/lib/systemd/system` 或 `/usr/lib/systemd/system` 的原生单元；缺失单元会报错。
原生服务在最终恢复阶段重启一次，Agent 启动成功后才标记总安装完成；任一步骤失败都会尝试停止所有受管服务。

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

Orin 总包在启动自己的 Agent 前停用 Robot 包附带的 `navi-supervisor-agent.service`，避免两个
Agent 争用 9080。生成的单元也声明服务冲突，并通过 `ExecStartPost` 等待本机健康检查返回匹配的
设备标识；30 秒内未就绪会让启动和总安装报错。这只验证 Agent HTTP 就绪，不代表各模块已经 RUNNING。

## Vision 服务

Orin Humble 已恢复 Vision `.run` 安装，生成 `navi-orin-vision-supervisor.service` 和网页注册。
`vision-preserve-shared` 安装策略保留已经安装的 `ros-humble-upperlimb-msgs`，不比较其与随包版本是否一致，
并将它从本次 Vision 安装、事务备份和版本恢复列表中排除。仅在未安装时使用随包版本；厂商运行验证继续执行。
适配只修改本次解压的安装器，不修改下载的原始工件；未识别的厂商脚本格式会在安装前报错。
Vision 进程以 `naviai` 用户加载厂商 `vision_environment.sh`，使用平台 DDS 配置。
Orin Jazzy 会在 Vision `.run` 安装成功后部署并启用
`navi-vision-supervisor.service`，由 systemd 启动 supervisord，再启动 Vision，并在本机 Agent 注册 19005。它保留独立的 ROS 运行环境：
`ROS_DOMAIN_ID=72`、`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`、
`ROS_LOCALHOST_ONLY=0`，并明确取消 `CYCLONEDDS_URI`。服务启动命令为：

```bash
ros2 launch navi_vision_pkg face_detection_node.launch.py \
  selected_camera:=auto camera_auto_timeout_sec:=8.0
```

Vision 只消费 Sensor 已发布的图像；Humble 当前版本在所需图像话题不可用时会启动失败，需先恢复相机。
Vision `.run` 已内嵌其 `*_dep.deb`、模型与运行时；总包不会再单独安装 Vision 依赖 DEB，避免
厂商安装器把重复预装状态判定为不安全的 legacy takeover。

Orin Humble 的云端 Orin common DEB 提供 Sensor 旧版依赖校验入口
`/usr/lib/orin-common-deb/install_deps.sh`。总包只按 `Orin common → Sensor common → Sensor .run` 的顺序
安装云端工件，不在构建阶段生成兼容 DEB。其余 target 的公共运行依赖仍由对应母盘提供；母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。

聚合界面的日志显示在对应进程下的独立面板中，服务状态刷新不会关闭面板；可手动刷新或关闭日志。
受管模块使用非登录 shell 执行命令，以保留启动脚本设置的虚拟环境和 ROS 日志路径。
Agent 密码初始化后会重启已安装的 chassis 服务（包括当前未运行的服务），使其重新生成 RPC 监听配置。

Chassis 使用原 `navi-orin-chassis.service` 接入 Supervisor（19004），替换厂商直接启动 ROS 的方式，避免并行启动重复节点。
启动参数保留原生服务的 `namespace:=zj_humanoid`。
