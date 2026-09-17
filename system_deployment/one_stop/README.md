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

### 附加配置目录与环境接口

`package-urls.json → targets.orin-humble.config_files` 配置目录复制；`source` 相对仓库根目录，复制目录内的内容到 `destination`，`owner` 指定设备上的属主（组名默认相同）。这个接口也用于随总包部署只读辅助脚本，例如 Livox 自动识别脚本。示例：

```json
{"source": "system_deployment/one_stop/assets/orin-humble/perception/config", "destination": "/home/naviai/navi_project/config/perception", "owner": "naviai"}
```

源文件统一放在 `one_stop/assets/<target>/<module>/`。当前目录如下，根目录原来的三个文件夹已迁移，设备安装位置不变：

```text
assets/orin-humble/
├── navigation/config/  → /home/naviai/navi_project/config/navigation/config
├── perception/config/  → /home/naviai/navi_project/config/perception
├── shared/schema/      → /home/naviai/navi_project/schema
└── diangosis/config/   → /home/naviai/navi_project/tool/diangosis
```

导航配置已包含 6 个文件：`explicit_config.json`、`planner_config.json`、`robot_params.rx.json`、`robot_params.wa1.json`、`robot_params.wa2.json`、`topic_config.yaml`。安装时同名文件始终覆盖，不会因目标文件已存在而跳过，旧内容保留编号备份。

后续可添加 `assets/pico-humble/display/config/`，然后在对应 target 的 `config_files` 增加映射。同名 `config` 通过平台和模块目录区分；每项须指定正确的设备目标目录，避免多个来源覆盖同一个目标文件。

构建内嵌文件并记录 SHA-256；在模块安装后、服务启动前覆盖部署，原文件保留 `.~1~` 等编号备份，不删除目标目录的其他文件。设备须已存在对应用户/组；源目录不接受符号链接。

配置编辑注册表是 `assets/orin-humble/shared/schema/version.json` 的 `ORIN.config`，不是总包版本文件 `one_stop/version.json`。已有 11 项配置；重启命令已映射为 `zj-humanoid-orin-navigation-supervisor.service` 和 `zj-humanoid-orin-naviai-nav2-supervisor.service`。当前该 schema 目录缺少 `navigation/topic_config.schema.json`，导航运行配置文件需由导航包提供；注册表不会自动生成这些文件。可用 `sudo python3 check_installation.py` 检查安装后的模板、目标文件及依赖包。

安装器环境使用包条目的 `environment`；模块运行环境使用包条目的 `runtime` 或 `supervisor.json` 中的 `source_files`、`environment`、`prelude`。导航环境首装时由 `environment-defaults/orin-humble/navigation.env` 创建为设备上的 `/etc/naviai/navigation/navigation.env`，并由 Humble `Middleware.env` 加载。该文件后续安装保留不覆盖，可直接手动修改。

- `ROBOT_TYPE`、`ROBOT_NAME`、`COMPOSE_PROFILES` 来自设备配置；`WA2_LS` 映射 `wa2`。
- `ROS_DOMAIN_ID` 默认 72，`RMW_IMPLEMENTATION` 默认 CycloneDDS。
- `NAVIGATION_ROBOT_MODEL` 默认取 `COMPOSE_PROFILES`，再回退 `wa2`；`NAVIGATION_CONFIG_PATH` 默认 `/home/naviai/navi_project/config/navigation`。
- Livox 服务启动前会调用 `/usr/lib/naviai/detect_livox_model.py`，使用已安装的 Livox SDK 和 `LIVOX_LIDAR_IP` 识别 `MID360` / `MID360S`，成功后写入 `/etc/naviai/navigation/lidar.auto.env`。默认雷达 IP 为 `192.168.217.17`；如果传感器网络调整，在 `navigation.env` 修改 `LIVOX_LIDAR_IP`。SDK 的无配置广播发现模式在当前 SDK 包中会段错误，已禁用。`Middleware.env` 在没有手动值时加载此自动结果；探测失败保留上次结果，不会阻止 Livox 服务启动。
- 如果网络拓扑不允许广播发现，或需要固定型号，在 `/etc/naviai/navigation/navigation.env` 中手动设置 `export LIDAR_3D_TYPE=MID360` 或 `MID360S`。手动值优先，服务启动时不再探测；不要直接修改 `lidar.auto.env`，它会在下一次成功探测时更新。
- `ROS_LOG_DIR` 按模块启动配置设置；当前导航为 `/var/log/naviai/navigation/ros`，公共交互 shell 不保证设置该值。

设备身份变量通过 Common 的配置工具修改，例如：

```bash
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-humble --robot-type WA2_LS \
  --robot-name zj_humanoid --ros-domain-id 72 --compose-profiles wa2
```

导航本地覆盖文件编辑后重启对应受管模块：

```bash
sudoedit /etc/naviai/navigation/navigation.env
sudo systemctl restart zj-humanoid-orin-navigation-supervisor.service
sudo systemctl restart zj-humanoid-orin-naviai-nav2-supervisor.service
```

修改 `LIDAR_3D_TYPE` 后还应重启 Livox 服务：

```bash
sudo systemctl restart zj-humanoid-orin-livox-lidar-supervisor.service
```

查看自动识别结果：

```bash
sudo cat /etc/naviai/navigation/lidar.auto.env
```

查看通用环境时可执行 `source /etc/naviai/Middleware.env`，再查看 `ROBOT_TYPE`、`ROBOT_NAME`、`ROS_DOMAIN_ID`、`RMW_IMPLEMENTATION`、`COMPOSE_PROFILES`、`NAVIGATION_ROBOT_MODEL`、`NAVIGATION_CONFIG_PATH`、`LIDAR_3D_TYPE`。`ROS_LOG_DIR` 只在具体模块启动命令中设置。

Orin Humble Common 依赖清单位于 `common/manifests/orin-humble/apt-packages.tsv`，已补充 Octomap、PCL、MCAP、SDL、FastAPI 等本次要求的依赖，ROS 包固定为 `ros-humble-*`。必须在 Ubuntu 22.04 arm64 构建机重新构建、发布 Common DEB，再重建总包；仅改清单不会更新云端旧 DEB。新增项最小版本为 0，具体可用版本和依赖闭包由构建机 APT 解析，需经目标母盘验证。

```text
1. 自动识别 OS、OS 版本和 CPU 架构（或使用 --target 指定）
2. 内嵌 system-config：写入 profile、Middleware、CycloneDDS 和设备身份
3. extra_debs：使用构建阶段已下载并内嵌的工件；同一 `install_group` 的 DEB 在一次 dpkg 事务中安装，再执行安装器
4. runs：按 package-urls.json 的顺序执行；仅明确声明支持该参数的包接收 `--robot-type`
5. supervisor：安装由 supervisor.json 定义的模块注册、`supervisord` 启动脚本和 systemd 服务
```

```text
Orin Humble：公共依赖 → 导航/底盘/工具 DEB 组 → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio → vision
Pico Humble：Pico common → upperlimb common → robot → upperlimb → display
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```

每个 target 的安装脚本会在写入配置、停止服务或执行 `dpkg` 前再次校验当前 OS、版本和 CPU 架构。
因此 `pico-humble` 的 payload 只能在 Ubuntu 20.04 amd64 上安装；即使手工传入 `--target pico-humble`，
也不能在 Orin 的 Ubuntu 22.04 arm64 上执行。

Orin Humble 在执行模块安装器前创建 Robot 的 `/var/lib/navi` 工作目录及日志目录，避免厂商服务
在执行 `ExecStartPre` 前因 `WorkingDirectory` 不存在而报 `200/CHDIR`。

## Supervisor 服务

### systemd 服务命名

总包创建的 systemd 单元统一采用以下名称，`<platform>` 仅为 `orin` 或 `pico`，`<module>` 为
`supervisor.json` 中的模块 `id`：

```text
zj-humanoid-<platform>-supervisor-agent.service
zj-humanoid-<platform>-<module>-supervisor.service
```

当前总包生成并启用的服务如下：

| 目标 | Agent | 模块服务 |
| --- | --- | --- |
| Orin Humble | `zj-humanoid-orin-supervisor-agent.service` | `zj-humanoid-orin-{manip-segmentation,manip-sam6d,manip-lingbot,manip-hand-detect,chassis,vanjee-lidar,livox-lidar,naviai-nav2,navigation,naviai-nav2-rawdata,diagnosis-system,web-rviz,robot,audio,vision}-supervisor.service` |
| Orin Jazzy | `zj-humanoid-orin-supervisor-agent.service` | `zj-humanoid-orin-vision-supervisor.service` |
| Pico Humble | `zj-humanoid-pico-supervisor-agent.service` | `zj-humanoid-pico-display-supervisor.service` |

下列服务由供应商模块包创建，属于 `external` 模块。总包只能注册、停启或重启它们，不能改名；改名需要
模块包同时修改自身的 `ExecStart`、安装脚本和配置路径：

| 目标 / 模块 | 原生服务名 |
| --- | --- |
| Orin Humble / Sensor | `navi-sensor-host.service` |
| Pico Humble / Robot | `navi-pico-robot-supervisor.service` |
| Pico Humble / Upperlimb | `navi-pico-upperlimb.service` |

所有旧 `navi-<platform>-*-supervisor.service` 和 `navi-<platform>-supervisor-agent.service` 都是迁移名称；
安装新版总包时会先停用它们，再创建并启动对应的 `zj-humanoid-*` 服务。Orin Jazzy 还会停用供应商旧
`navi-vision-supervisor.service`，两套服务不会同时运行。

Pico Humble 的 `display` 使用 `managed` 模式，RPC 为 `192.168.217.66:19004`，服务为 `zj-humanoid-pico-display-supervisor.service`。启动优先级为 1，早于 Robot（10）和上肢（20）；此顺序用于总包安装收尾启动，不保证开机时不同 systemd 服务的启动顺序。`autorestart: "true"`、`startsecs: 0` 使已创建的进程正常或异常退出后持续重启，避免快速退出耗尽启动重试。人工停止仍有效，进程卡住或子节点退出但 launch 存活时不会自动恢复画面。启动时先加载公共 `/etc/nav01/Middleware.env`，再依次加载 `/opt/ros/humble/setup.bash` 和 `/opt/navi_display/ros/setup.bash`，执行 `ros2 launch media_play media_play.launch.py`；通过 Agent `:9080` 查看及启停。安装器暂按无参数调用配置；图形会话所需的环境变量若有额外要求，需由模块方提供。

Orin Humble 的 `manip` RUN 使用 `--force` 安装参数。厂商安装器在发现其内嵌的
`ros-humble-navi-manip-msgs` 已安装时会拒绝默认重装；`--force` 允许在相同发布基线或经确认的升级
场景覆盖其内嵌 Manipulation DEB。其四个功能分别由 Supervisor 管理：

| 模块 ID | RPC 端口 | 启动脚本（位于 `/opt/naviai/manip/functions/bin/`） |
| --- | --- | --- |
| `manip-segmentation` | 19013 | `start-segmentation.sh` |
| `manip-sam6d` | 19014 | `start-sam6d.sh` |
| `manip-lingbot` | 19015 | `start-lingbot.sh` |
| `manip-hand-detect` | 19016 | `start-hand-detect.sh` |

四项以 root 执行，均先加载 `/etc/naviai/Middleware.env`，再执行模块脚本。该统一入口加载设备身份和对应 ROS 环境，并从 `/etc/zj_humanoid/device.env`、`/etc/zj_humanoid/cyclonedds.xml` 提供 `ROS_DOMAIN_ID`、`RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`、`ROS_LOCALHOST_ONLY=0`、`CYCLONEDDS_URI`、`ROBOT_TYPE` 和 `ROBOT_NAME`。不要在 Manip 脚本或 Supervisor 命令中再写死 Domain 或 DDS URI；以设备统一环境为准，才能与 Sensor 的相机话题处于同一 DDS 网络。通过 Agent `:9080` 查看和启停。脚本需保持前台运行；若安装器另行启用原生服务，需确认服务名后停用，避免重复启动。

`manip-lingbot` 依赖头部 RealSense 的 `CameraInfo`，因此安装收尾时排在 `navi-sensor-host.service` 之后启动，并设置 `autorestart: "true"`。相机尚未完成初始化时，Lingbot 会自行重试，后续安装或开机不需要人工重启。人工通过 Supervisor 停止时仍保持停止，直到手动启动或重启其 systemd 服务。

不要再维护独立的 `supervised_stack` manifest。`supervisor.json` 的 target 内：

- `supervisor` 是 Supervisor Agent 的 RPC、密码、运行目录和模块目录约定；
- `supervisor_modules` 定义模块端点和受管服务；`external` 仅注册既有服务的 RPC，`managed` 会生成
  `supervisord`、对应 systemd 单元及 Agent 的模块 JSON，`systemd` 仅用于保留 DEB 已提供的原生 systemd 服务。

Orin Humble 注册 chassis、两个雷达、导航、Nav2、原始数据、诊断、Web RViz、sensor、robot、audio、vision。导航、底盘与工具
DEB 组的八项业务服务以 `managed` 模式接入：总包生成独立 supervisord、Agent RPC 及 systemd 外层服务。总包不安装 `zj-humanoid-services`；若旧设备已有其 unit，会停用这些供应商原服务以避免重复启动。Orin 的 robot 由总包生成
`zj-humanoid-orin-robot-supervisor.service` 并使用 `19002`；Sensor 已由自身安装包原生维护
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

Pico Robot 与 Display 安装器按无自定义参数调用（`"arguments": []`）；Pico 上肢安装器需要机型，必须
显式配置 `"arguments": ["--", "--robot-type", "{robot_type}"]`。总包会在调用它们前写入
`/etc/zj_humanoid/device.env`。不要省略 `arguments`：Pico Robot 不支持该选项，而上肢必须接收该选项。

总包在每个厂商 RUN 前加载目标的统一 `Middleware.env`，因此安装脚本可读取设备身份、ROS、Domain 72 和
CycloneDDS 环境。RUN 完成后会重新部署并加载总包的标准环境，避免厂商安装器覆盖
`/etc/nav01/Middleware.env` 或 `/etc/naviai/Middleware.env` 后影响后续模块和最终服务启动。

Common DEB 由目标匹配的独立构建机生成并发布：Pico Humble Common 在 Ubuntu 20.04 amd64 构建，
Orin Humble Common 在 Ubuntu 22.04 arm64 构建。总包不触发这些远程构建；它只从云端下载已发布的
Common DEB。Pico Common 是 PICO 的首个依赖 DEB，随后安装 upperlimb-common 与 robot。

`extra_debs` 默认用 `dpkg -i` 安装。仅当上游 DEB 缺少 `Replaces`、但必须覆盖已知旧包文件时，才可为
该条目设置 `"force_overwrite": true`；它必须不属于 `install_group`，总包会只对这一份 DEB 执行
`dpkg --force-overwrite -i`。当前 Orin Humble 的远端 `naviai-log` 用于替换旧
`zj-humanoid-naviai-log-compat`，是唯一启用项；不要将此选项复制给其他包。

`/etc/zj_humanoid/device.env` 是物理设备身份和机型配置，由总包的 system-config 在安装开始时通过
`deploy_common.py configure` 写入；Orin Humble Common 不再将它作为 DEB conffile 交付。升级 Common
不会询问是否用模板覆盖设备身份。手动恢复或修改时，使用相同的 `configure` 命令，不要直接从包内模板覆盖。

当 target 声明 `supervisor` 时，总包会同时内嵌并安装对应的 Supervisor Agent；不再依赖另一份
Agent `.run` 包。Audio 的 `start_policy: "supervisor"` 只会抑制厂商 `.run` 最后一条前台 ROS
启动命令，依赖安装内容和其余安装逻辑保持原样，随后由 `zj-humanoid-orin-audio-supervisor.service` 启动。

Orin 总包在启动自己的 Agent 前停用 Robot 包附带的 `navi-supervisor-agent.service`，避免两个
Agent 争用 9080。生成的单元也声明服务冲突，并通过 `ExecStartPost` 等待本机健康检查返回匹配的
设备标识；30 秒内未就绪会让启动和总安装报错。这只验证 Agent HTTP 就绪，不代表各模块已经 RUNNING。

## Vision 服务

Orin Humble 已恢复 Vision `.run` 安装，生成 `zj-humanoid-orin-vision-supervisor.service` 和网页注册。
安装会在 Vision RUN 前后停用旧的 `navi-vision.service` 与 `navi-vision-supervisor.service`；最终只由
`zj-humanoid-orin-vision-supervisor.service` 监听 19005，避免旧服务使用旧 RPC 密码占用端口。
`vision-preserve-shared` 安装策略保留已经安装的 `ros-humble-upperlimb-msgs`，不比较其与随包版本是否一致，
并将它从本次 Vision 安装、事务备份和版本恢复列表中排除。仅在未安装时使用随包版本；厂商运行验证继续执行。
适配只修改本次解压的安装器，不修改下载的原始工件；未识别的厂商脚本格式会在安装前报错。
Vision 进程以 `naviai` 用户加载厂商 `vision_environment.sh`，使用平台 DDS 配置。
Orin Jazzy 会在 Vision `.run` 安装成功后部署并启用
`zj-humanoid-orin-vision-supervisor.service`，由 systemd 启动 supervisord，再启动 Vision，并在本机 Agent 注册 19005。它保留独立的 ROS 运行环境：
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
`/usr/lib/orin-common-deb/install_deps.sh`。部分旧版 Robot 安装器还会用 `dpkg-query` 检查真实包名
`orin-common-deb`，而不识别 Debian `Provides`。因此 Common 构建会同时产出无文件的过渡 DEB
`orin_common_deb_2.0.0-release-humble-arm64.deb`；它依赖真实基础包 `navi-common-dep`，总包固定按
`Orin common → Orin common compatibility → Sensor common → Robot common → 模块 RUN` 的顺序安装。两份
Common DEB 必须一同发布到制品服务器。其余 target 的公共运行依赖仍由对应母盘提供；母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。

聚合界面的日志显示在对应进程下的独立面板中，服务状态刷新不会关闭面板；可手动刷新或关闭日志。
受管模块使用非登录 shell 执行命令，以保留启动脚本设置的虚拟环境和 ROS 日志路径。
Agent 密码初始化只写入固定凭据。安装收尾阶段会按 `startup_priority` 启用、重启并检查所有纳入管理的 Supervisor 服务。

Chassis、导航及工具 DEB 的业务命令由生成的 supervisord 启动，均拥有独立 RPC 端口；使用 `:9080` 统一查看和控制它们。
