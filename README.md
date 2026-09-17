# Navi 系统交付

## 整体思路

- **基础环境**：母盘提供操作系统、ROS 和平台公共依赖。
- **模块依赖**：目前随各模块安装包交付，后续统一管理依赖；模块运行环境已提供统一配置接口，见下文。
- **交付方式**：当前只输出一个 `.run` 包，一键安装后自动启动目标设备的全部已配置模块。环境与功能稳定后，计划转为 middleware 包。

母盘需预装对应系统、ROS 和平台公共依赖：

| target | 系统 / ROS | 架构 |
| --- | --- | --- |
| `orin-humble` | Ubuntu 22.04 / Humble | arm64 |
| `orin-jazzy` | Ubuntu 24.04 / Jazzy | arm64 |
| `pico-humble` | Ubuntu 20.04 / 公司 ROS Humble | amd64 |
| `pico-jazzy` | Ubuntu 24.04 / Jazzy | amd64 |
| `rdk-jazzy` | RDK OS V5.1.0 / Jazzy（当前仅交付依赖） | arm64 |

## 构建与安装

在 `system_deployment/one_stop/package-urls.json` 配置模块 URL、安装顺序和环境参数，在同目录 `supervisor.json` 配置服务，`version.json` 设置 `version`、`output_name`。构建机需能访问配置中的制品服务器，在仓库根目录执行：

```bash
./system_deployment/one_stop/build_release.sh
# 输出：dist/navi_one_stop_installer-<version>.run
```

将安装包复制到已准备好母盘的设备：

```bash
sudo ./navi_one_stop_installer-<version>.run -- --robot-type WA1
```

自动识别系统与架构；已配置机型的设备可省略 `--robot-type`。机型定义在 `system_deployment/common/configs/robot-types.json`，当前包括：

```text
H1、U1、U2_WA1、U2-S、U2-D
I2、I2-S、I2-D、I2-E、I3-S
WA1、WA1_400K、WA1_400L、WA1-S、WA1-D、WA1-E
WA2、WA2_L、WA2_LS、WA2_TY20、WA2-S、WA2-P、WA2-D
ZYD、ZYD_V1、JK、JK2_V1
```

安装失败时会尝试停止本次纳入管理的服务，**不会恢复已安装的软件、已执行的模块安装器或已修改的配置**，设备可能处于部分更新状态。恢复需使用事先验证的方案；停服不等于回滚。

## 打包配置使用方法

配置按分工拆为两份，构建入口仍为 `build_release.sh`：

| 文件 | 维护内容 |
| --- | --- |
| `package-urls.json` | 开发者维护模块 URL、版本、安装 `arguments` / `environment` 及运行 `runtime`；平台字段由交付维护者维护 |
| `supervisor.json` | 交付维护者维护 Agent、服务、端口、启动命令、`start_policy` 和旧包迁移策略 |

两份文件按 target 和模块名关联，默认 `runs.name = supervisor_modules.id`；不一致时在 Supervisor 条目中设置 `"package": "实际工件 name"`。工件可以是 RUN 或 DEB。构建会校验关联关系及 XML-RPC 端口冲突，发布清单记录 `supervisor_config_sha256`。

开发者可在自己的 `runs` 条目里配置：

```json
{
  "name": "robot",
  "url": "http://<制品服务器>/robot.run",
  "arguments": ["--", "--config", "/etc/naviai/robot/config.yaml"],
  "environment": {"ROBOT_INSTALL_CONFIG": "/etc/naviai/robot/config.yaml"},
  "runtime": {
    "source_files": ["/etc/naviai/robot/site.env"],
    "environment": {"ROBOT_CONFIG_FILE": "/etc/naviai/robot/config.yaml"},
    "unset_environment": ["PYTHONPATH"]
  }
}
```

这是接口示例，参数名与环境变量名必须由模块安装器/启动程序实际支持，不会自动转换为 ROS 参数。`arguments` 按原样传给安装器（仅独立的 `{robot_type}` 占位符自动替换），`environment` 仅传给本次安装器及其子进程，不影响其他模块。

`runtime` 用于 `managed` 模块：公共启动环境加载后，依次清除变量、加载开发者环境脚本、导出开发者变量，最后执行运维配置的 `prelude` 和业务命令。运维 `prelude` 可作最终覆盖。配置文件和环境脚本路径均指向目标设备，文件需由模块包交付或提前准备，本接口不复制文件。脚本应使用 `export` 导出变量。

`external` 模块的运行环境由其原生服务维护，不能通过此 `runtime` 修改；应由模块启动脚本读取自己的配置文件。此类误配会在构建时报错。环境参数变更后需重新打包安装；敏感凭据不要写入这些随总包交付的配置。

以下操作在仓库根目录完成。修改已有目标时，在 `system_deployment/one_stop/package-urls.json` 找到 `targets.orin-humble` 等对应条目；下面的示例是该目标内的配置片段，不要覆盖整个文件。

### 1. 配置模块安装包

模块可以以 RUN 或 DEB 交付。RUN 放在 `runs`；DEB 放在 `extra_debs`。两者均可在 `supervisor.json` 关联为受管模块；`runs` 按数组顺序在 DEB 之后安装。以 RUN 形式的 Sensor 为例，替换下面的 URL 和版本；摘要可留空自动计算：

```json
{
  "name": "sensor",
  "version": "2.0.0-2",
  "url": "http://<制品服务器>/<实际路径>/sensor.run",
  "sha256": "",
  "arguments": ["--", "--robot-type", "{robot_type}"]
}
```

`sha256` 留空时构建自动计算；可选填写已知摘要用于锁定下载内容。`arguments` 必须与模块安装器接口一致：支持机型参数时使用上述写法，完全不接收参数时写 `[]`；省略该字段默认传入机型参数。`{robot_type}` 在设备安装时替换为实际机型。

DEB 模块配置示例：

```json
{
  "name": "robot-deb",
  "version": "2.0.0-2",
  "url": "http://<制品服务器>/<实际路径>/robot.deb",
  "sha256": "",
  "installers": ["/usr/lib/naviai/robot/configure.sh"],
  "environment": {
    "ROBOT_INSTALL_CONFIG": "/etc/naviai/robot/config.yaml"
  },
  "runtime": {
    "source_files": ["/etc/naviai/robot/robot.env"],
    "environment": {
      "ROBOT_CONFIG_FILE": "/etc/naviai/robot/config.yaml"
    }
  }
}
```

构建时先执行 `env ... dpkg -i robot.deb`，再执行 `installers` 中的绝对路径脚本；`environment` 会传给 DEB 的 maintainer scripts 和这些安装脚本。`installers: ["auto"]` 会从 DEB 中识别唯一的依赖安装脚本；不需要后处理脚本时写 `[]`。DEB 没有 `arguments`，需要的安装配置应通过 DEB 自身的默认配置、`environment` 或其安装脚本读取。

`runtime` 的行为与 RUN 模块相同，但只适用于由总包 `managed` 的模块。随后在 `supervisor.json` 指定实际工件名称：

```json
{
  "id": "robot",
  "package": "robot-deb",
  "mode": "managed",
  "port": 19002,
  "working_directory": "/var/lib/naviai/robot",
  "command": "/opt/naviai/robot/bin/start.sh"
}
```

`package` 可省略，省略时默认等于 `id`。现有目标允许同名依赖 DEB 和 RUN，默认关联同名 RUN 以保持兼容；要让 Supervisor 接管 DEB，DEB 的 `name` 必须使用独立名称，例如 `robot-deb`。仅修改下载地址不会自动添加服务管理，新增模块还需完成下一步。

### 2. 配置模块启动方式

在 `supervisor.json` 同一目标的 `supervisor_modules` 中配置。`id` 必须唯一；只有 `managed` 与 `external` 使用且要求本机 `port` 唯一。

**模块已有 Supervisor：使用 external。** Sensor 当前配置如下：

```json
{
  "id": "sensor",
  "description": "Navi Orin Sensor stack",
  "mode": "external",
  "port": 19001,
  "restart_service": "navi-sensor-host.service",
  "native_rpc_config": "/etc/naviai/navi-sensor-host-supervisor.conf"
}
```

这两个路径/名称不能凭空约定，需要先在装过该模块的目标设备上确认：

```bash
systemctl cat navi-sensor-host.service
sudo sed -n '1,160p' /etc/naviai/navi-sensor-host-supervisor.conf
```

检查服务的 `ExecStart`；若指向启动脚本，继续检查脚本实际传给 supervisord 的 `-c` 路径。`native_rpc_config` 必须指向它真正加载的配置。该文件由模块包提供，且须已有 `[rpcinterface:supervisor]`；总包只备份并补齐认证 TCP RPC，保留原进程定义，再重启 `restart_service`。文件缺失会导致安装失败。

如果模块启动脚本每次都会重写该配置，应由模块自身生成正确的 RPC 地址及 `agent / 1` 凭据，并省略 `native_rpc_config`，避免总包补写的内容被覆盖。

**模块没有 Supervisor：使用 managed。** 例如新增模块时，按实际启动命令配置：

```json
{
  "id": "example",
  "description": "Example ROS module",
  "mode": "managed",
  "port": 19006,
  "working_directory": "/var/lib/naviai/example",
  "source_files": ["/etc/naviai/Middleware.env"],
  "command": "ros2 launch example_pkg bringup.launch.py"
}
```

总包生成 systemd 服务、supervisord 配置及 Agent 注册；模块包需提供命令使用的软件和环境脚本，并避免原服务重复启动同一业务进程。需要非 root 运行时，参考现有 Vision 条目配置运行用户及目录权限；环境变量用法见“中间件环境配置”。

**模块包已提供 systemd 服务：可使用 systemd。** 这是保留厂商 unit 的兼容模式；它不是 supervisord，因此不配置 `port`、`command` 或 `runtime`。若要求业务进程统一由 supervisord 守护，则不要安装提供厂商 unit 的 DEB，或在 `managed` 模块中通过 `disable_services` 停用其原服务后配置真实启动命令。

```json
{
  "id": "example-native-service",
  "description": "保留厂商 systemd 服务的示例",
  "mode": "systemd",
  "systemd_service": "vendor-example.service"
}
```

Orin Humble 当前的导航、底盘与工具 DEB 会分别注册为 8 个 `managed` 模块：`chassis`、`vanjee-lidar`、`livox-lidar`、`naviai-nav2`、`navigation`、`naviai-nav2-rawdata`、`diagnosis-system`、`web-rviz`。它们不是一个 chassis 模块。总包不安装 `zj-humanoid-services`，并会停用已存在的同名厂商 unit；随后生成 8 个独立的 supervisord 服务和 XML-RPC 端口。Nav2 DEB 的后台安装通过 `wait_for_packages` 等待最多 120 秒，确认 `zj-humanoid-ros-humble-naviai-nav2-bringup` 已安装后才开始所有受管服务。这些相互依赖的 DEB 通过 `install_group: "navigation-chassis-services"` 放进同一次 `dpkg -i`。

两种模式均需目标具有 `supervisor` 配置。已有 Orin / PICO 配置可复用；新增时需设置设备实际 `internal_ip`、`agent_service`、`agent_modules_directory`、`agent_password_file`、`module_root`、`runtime_root`、`log_root`，路径均为目标设备路径，不是构建机路径。

### PICO 模块启动优先级

在 `supervisor.json` 的 PICO `supervisor_modules` 条目中使用 `startup_priority`。数字越小，启动越早；总包按该值顺序启动，并在启动下一项前确认前一服务已处于 `active`。当前 PICO Robot 为 10、上肢为 20：Robot 启动成功后才启动上肢。

```json
{
  "id": "robot",
  "mode": "external",
  "restart_service": "navi-pico-robot-supervisor.service",
  "startup_priority": 10
}
```

省略时默认优先级为 100；相同优先级按服务名排序，不能表达依赖关系。`active` 仅表示 systemd 服务已启动，不等于模块业务已就绪；如果下一模块必须等待硬件、ROS 节点或话题就绪，应在前置模块提供健康检查，并将该检查接入后续启动流程。

### Orin Humble：Robot / Vision 的实际启动配置

二者在 `supervisor.json → targets.orin-humble.supervisor_modules` 中分别以 `id: "robot"`、`id: "vision"` 配置，均为 `managed`。修改启动命令用 `command`，环境脚本用 `source_files`，启动前目录准备用 `prelude`，进程异常重启策略用 `autorestart`。

| 项目 | Robot | Vision |
| --- | --- | --- |
| systemd 服务 | `zj-humanoid-orin-robot-supervisor.service` | `zj-humanoid-orin-vision-supervisor.service` |
| 工作目录 | `/var/lib/navi` | `/var/lib/navi-vision/supervised` |
| 业务运行用户 | root | naviai（通过 `runuser` 切换） |
| 环境加载 | `/etc/naviai/Middleware.env`、`/etc/naviai/robot/robot_env.sh` | `/etc/naviai/Middleware.env`、`/usr/lib/orin-vision-common-deb/vision_environment.sh` |
| 最终 ROS 命令 | `ros2 launch orin_robot orin_robot.launch.py` | `ros2 launch navi_vision_pkg face_detection_node.launch.py selected_camera:=auto camera_auto_timeout_sec:=8.0` |
| RPC 端口 | 19002 | 19005 |

总包根据这些字段生成并安装下列文件，`<module>` 分别为 `robot` 或 `vision`：

```text
/etc/systemd/system/zj-humanoid-orin-<module>-supervisor.service
  → /etc/naviai/supervised-stack/<module>/supervisor-entrypoint.sh
    → 生成 /run/naviai/<module>/supervisord.conf 并启动 supervisord
      → /etc/naviai/supervised-stack/<module>/launch.sh
        → 加载环境、准备目录、执行业务命令
```

Agent 注册文件位于 `/etc/naviai/supervisor-agent/modules.d/<module>.json`。这些文件由总包生成，不需要使用者手写；尤其不要修改 `/run/naviai/<module>/supervisord.conf`，服务重启会重新生成。永久修改应回到 `supervisor.json`，重新打包安装。

设备上查看实际启动配置：

```bash
systemctl cat zj-humanoid-orin-robot-supervisor.service
sudo cat /etc/naviai/supervised-stack/robot/launch.sh
sudo cat /run/naviai/robot/supervisord.conf
# 查看 Vision 时，将以上 robot 替换为 vision
```

Vision 的 `prelude` 在降权前设置日志父目录和子目录的属主，确保 `naviai` 能写入 `/var/log/naviai/vision/ros`。上述路径仅针对 Orin Humble；PICO Robot 是模块包自带 Supervisor 的 `external` 模式。

### 3. 设置版本、构建和验收

在 `system_deployment/one_stop/version.json` 同步修改：

```json
"version": "2.0.0-2",
"output_name": "navi_one_stop_installer-2.0.0-2"
```

然后执行：

```bash
./system_deployment/one_stop/build_release.sh --dry-run
./system_deployment/one_stop/build_release.sh
```

`--dry-run` 不下载工件，不验证 URL 可达性或目标设备上的文件是否存在。正式构建成功后，将产物复制到对应测试设备，先运行 `-- --pretest`，再按上文安装；通过 `navi-version`、Agent 页面和业务用例确认版本、服务及功能。

## 版本与发布身份

版本采用 `功能版本-交付修订号`，当前总包为 `2.0.0-1`，产物为 `dist/navi_one_stop_installer-2.0.0-1.run`。`2.0.0` 表示功能版本，`-1` 表示第 1 次交付修订；模块、依赖或配置变更后递增为 `-2`、`-3`。同一发布版本的内容应固定，`build_id` 和 SHA-256 继续用于构建追溯。

总包和模块独立维护版本，模块修订号不随总包统一递增；发布清单记录实际组合。更新总包版本时同步修改 `version.json` 中的 `version` 和 `output_name`。

每次构建在总包内生成 `release-manifest.json`，包含所有目标；安装时选取目标清单并填入实际机型。数据约定：

| 字段 | 含义 |
| --- | --- |
| `schema_version`、`release` | 清单格式版本、总包版本 |
| `build_id`、`built_at` | UTC 构建时间加随机标识、构建时间；同版本重建也能区分 |
| `git_commit`、`git_dirty` | 构建仓库提交及是否存在未提交修改 |
| `version_config_sha256`、`package_config_sha256`、`builder_sha256` | 发布配置和构建脚本的内容身份 |
| `target`、`platform`、`robot_type` | 目标、系统与架构、安装设备机型 |
| `modules` | 按模块名记录声明版本、来源 URL、包内路径和实际 SHA-256 |
| `artifacts`、`payload_checksums` | 全部依赖与模块工件、内嵌配置及辅助文件的内容摘要 |
| `offline_installation` | 当前固定为 `unverified`，表示尚未取得断网安装验收结论 |

在 `package-urls.json` 各模块的 `runs` 条目中增加 `"version": "实际模块版本"`。版本由模块发布方提供；未填写则记录 `null`、显示 `unknown`，仍可用工件 SHA-256 区分。正式发布前须补全版本并核对工件内容。

安装后通过以下命令查询，无需人工拼接包列表和服务状态：

```bash
navi-version
navi-version --json
```

记录固定保存在 `/var/lib/naviai/release/`：

- `current.json`：最后成功安装的目标清单，包含实际机型和完成时间。
- `previous.json`：上一次成功清单，仅供追溯，不是回滚备份。
- `status.json`：最近一次安装尝试，状态为 `installing`、`complete` 或 `failed`。

只有安装与服务启动步骤成功后才更新 `current.json`；失败保留旧清单，断电等中断可能保留 `installing`。`navi-version` 对失败、中断或没有成功记录返回非零退出码，提示系统可能部分更新。它记录安装基线，不证明进程健康，也不会自动识别绕过总包的单模块替换；此类变更必须随 Bug 反馈单独说明。跨设备联调需同时提供 Orin 和 PICO 的查询结果。

## 联网与离线交付

| 阶段 | 当前能力 |
| --- | --- |
| 构建阶段 | 配置使用远程 URL 时需要联网访问制品服务器 |
| 目标安装阶段 | 总包已内嵌配置的工件，但是否仍需联网取决于模块安装脚本；当前不能承诺完全离线 |
| 正式 middleware 交付要求 | 在指定母盘上完全断网可安装、重启并通过业务验收，作为发布门槛 |

本仓库 Common 离线安装逻辑使用 `apt-get --no-download`，但不代表所有厂商模块都满足离线要求。必须逐包审计安装脚本及其调用链中的 `apt install`、`pip install`、`wget`、`curl` 等：本地依赖安装可以保留，所需系统包、Python wheel、模型及其他下载资源必须随包或母盘提供，禁止安装时补拉网络资源。

离线验收应在指定母盘的干净设备上关闭公网和制品服务器访问、排除下载缓存影响，执行安装、重启及模块功能测试，并记录母盘版本、总包 build ID、模块组合和结果。缺失依赖须在系统变更前报告；相关完整预检仍待实现。`--pretest` 目前用于版本预检，不是离线能力认证。

## 中间件环境配置

总安装包写入以下配置。`Middleware.env` 加载设备身份、对应 ROS 环境和 DDS 通信设置；默认使用 Domain 72、CycloneDDS，允许跨主机通信，模块可按自身要求覆盖。

| 配置 | 设备上的位置 |
| --- | --- |
| 中间件环境入口 | Orin / RDK：`/etc/naviai/Middleware.env`；PICO：`/etc/nav01/Middleware.env` |
| 设备身份、机型及通信配置 | `/etc/zj_humanoid/device.env` |
| DDS 配置 | `/etc/zj_humanoid/cyclonedds.xml` |
| Shell 公共环境入口 | `/etc/profile.d/zj_humanoid.sh` |

公共环境模板位于 `system_deployment/common/templates/Middleware.{orin,pico,rdk}.env`，目标与机型定义位于 `system_deployment/common/configs/`。手动调试时执行：

```bash
source /etc/naviai/Middleware.env  # Orin / RDK
# PICO 使用：source /etc/nav01/Middleware.env
```

模块运行环境在 `supervisor.json` 的 `targets.<target>.supervisor_modules` 中配置。以现有 Orin robot 模块为例，修改对应条目的以下字段，其他字段保留：

```json
{
  "unset_environment": ["PYTHONPATH"],
  "environment": {"RCUTILS_COLORIZED_OUTPUT": "0"},
  "source_files": [
    "/etc/naviai/Middleware.env",
    "/etc/naviai/robot/robot_env.sh"
  ],
  "prelude": ["export RCUTILS_COLORIZED_OUTPUT=0"]
}
```

启动时依次执行：清除 `unset_environment` → 导出 `environment` → 按顺序加载 `source_files` → 执行 `prelude` → 运行 `command`。后加载的配置可以覆盖前面的值；必须最终生效的设置放在 `prelude`，`environment` 的值须为字符串。引用的环境脚本需由模块安装包提供。

此接口用于 `mode: "managed"` 的模块。修改后重新构建并安装总包生效；`mode: "external"` 的模块由自身服务加载环境，需在模块自己的启动配置中接入 `Middleware.env`。

## 运行架构与观测

systemd 管理服务生命周期；Agent 统一聚合总包生成的 `managed` supervisord、模块包原生的 `external` supervisord，以及可选的模块包原生 `systemd` 服务：

```text
systemd
├── Supervisor Agent :9080
├── managed 模块 → supervisord → 业务进程
├── external 模块 → 模块原生 supervisord → 业务进程
└── 可选 systemd 模块 → 原生 systemd 服务

Web → Orin Agent :9080 → Orin XML-RPC 模块 :19001～19012
                      → PICO Agent :9080 → PICO 模块 XML-RPC
```

`managed` 模块的 supervisord 和 systemd 配置由总包生成，名称统一为 `zj-humanoid-<orin|pico>-<module>-supervisor.service`；聚合服务统一为 `zj-humanoid-<orin|pico>-supervisor-agent.service`。`external` 模块复用模块包自己的 Supervisor，服务名由模块包决定；`systemd` 模块保留 DEB 的原生服务，由 Agent 通过 systemctl/journald 观测和控制。Humble 和 Jazzy Vision 均采用 systemd → supervisord → Vision；Jazzy 已迁移为 `zj-humanoid-orin-vision-supervisor.service`，通过 19005 接入本机 Agent。

后续新增模块必须通过 `supervisor_modules` 接入统一管理和观测：由总包托管为 `managed`、注册模块原生 Supervisor 为 `external`，或注册模块包原生 systemd 服务为 `systemd`。不再新增未被 Agent 注册的直接启动路径。其他尚未接入的目标模块仍需逐项迁移，不代表目前所有目标已统一完成。

Humble 当前支持聚合与模块独立观测，均可查看状态、日志及启停进程。

Orin Humble 导航、底盘与工具服务映射如下。每项都由独立 supervisord 管理，并通过 `:9080` 统一展示；供应商原 systemd 服务只作为迁移时停用的兼容项。

| Agent 模块 | Supervisor 服务 / 端口 | 供应商原 systemd 服务（停用） |
| --- | --- | --- |
| `navigation` | `zj-humanoid-orin-navigation-supervisor.service` / 19009 | `zj-humanoid-navigation.service` |
| `chassis` | `zj-humanoid-orin-chassis-supervisor.service` / 19004 | `zj-humanoid-chassis.service` |
| `vanjee-lidar` | `zj-humanoid-orin-vanjee-lidar-supervisor.service` / 19006 | `zj-humanoid-vanjee-lidar.service` |
| `livox-lidar` | `zj-humanoid-orin-livox-lidar-supervisor.service` / 19007 | `zj-humanoid-livox-lidar.service` |
| `naviai-nav2` | `zj-humanoid-orin-naviai-nav2-supervisor.service` / 19008 | `zj-humanoid-naviai-nav2.service` |
| `naviai-nav2-rawdata` | `zj-humanoid-orin-naviai-nav2-rawdata-supervisor.service` / 19010 | `zj-humanoid-naviai-nav2-rawdata.service` |
| `diagnosis-system` | `zj-humanoid-orin-diagnosis-system-supervisor.service` / 19011 | `zj-humanoid-diagnosis-system.service` |
| `web-rviz` | `zj-humanoid-orin-web-rviz-supervisor.service` / 19012 | `zj-humanoid-web-rviz-ros2.service` |

Jazzy 本次接入 Vision；其 Agent 当前仅注册本机 Vision，不套用 Humble 的其他模块及 PICO 聚合配置。

- **聚合页面**：`http://<Orin设备IP>:9080` 查看 Orin 和 PICO 模块；`http://192.168.217.66:9080` 查看 PICO 本机模块。无需账号密码，仅用于可信内网。
- **模块 RPC 页面**：访问下表端口，用户名为 `agent`，固定密码为 `1`；测试和运维仍优先使用 `:9080`。

| 设备 IP | 模块端口 |
| --- | --- |
| Orin：`192.168.217.100` | sensor 19001、robot 19002、audio 19003、chassis 19004、vision 19005、vanjee 19006、livox 19007、nav2 19008、navigation 19009、rawdata 19010、diagnosis 19011、web-rviz 19012 |
| PICO：`192.168.217.66` | robot 19002、upperlimb 19003、display 19004 |

浏览器可打开 `http://192.168.217.100:19002`（Orin Robot 示例），输入用户名 `agent`、密码 `1`。浏览器所在机器需能访问该设备内网 IP。已有设备重新安装新版总包后，旧密码会更新为 `1`。

测试与运维优先使用 `:9080`；`19001～` 为模块 RPC / 研发调试入口。后续可限制这些端口仅允许 Agent 访问，当前尚未实施该访问限制。

## 日志规范（待确认）

统一日志目录、文件命名及轮转保留规则尚未确定；当前沿用各模块配置，可通过 Supervisor 查看日志。最终存放位置确认后补充。

## 测试与运维交付（待完善）

安装后可将 `system_deployment/check_installation.py` 单独复制到设备，执行只读检查：

```bash
sudo python3 check_installation.py
# 可指定预期机型；保存机器可读报告：
sudo python3 check_installation.py --robot-type WA1 --json > installation-check.json
```

脚本自动识别目标，检查配置文件、发布记录、服务运行及自启、Agent 页面、模块状态及日志接口、RPC 认证和 Vision 日志目录权限。输出 PASS / FAIL / WARN，有 FAIL 时退出码为 1；Orin 和 PICO 应分别运行。首次安装没有 `previous.json` 属正常情况。脚本不启停服务、不加载环境脚本、不执行 ROS 业务；不能代替业务用例或离线安装验收。

每次发布应提供可追溯的交付资料，让测试和运维能够完成安装、验收、排障和恢复：

### 必须交付的文档

以下文档应随正式版本一起交付，标明适用总包版本、build ID、维护人和更新时间。README 是项目入口，不能代替测试与运维操作手册。下列文件名为交付约定，标为待编写的文档尚未提供。

| 文档 | 建议文件名 | 使用对象 | 必须包含的内容 | 当前状态 |
| --- | --- | --- | --- | --- |
| 交付清单 | `交付清单.md` | 测试、运维 | 安装包、母盘、工具和文档列表；版本、校验值、获取位置；适用机型与目标平台 | 待编写 |
| 版本发布说明 | `版本发布说明.md` | 测试、运维 | 总包及模块组合、变更内容、修复 Bug、已知问题、兼容范围、升级前置条件；关联发布清单 | 已有机器可读清单，说明文档待编写 |
| 安装与升级手册 | `安装与升级手册.md` | 运维、测试 | 母盘要求、首次安装与升级步骤、机型配置、联网要求、预检命令、成功判据、失败处理 | README 有基础命令，独立手册待编写 |
| 配置手册 | `配置手册.md` | 运维、联调人员 | 网络与设备身份、ROS / DDS、模块环境配置；每项默认值、修改位置、生效方式及备份方法 | README 部分覆盖，待补齐 |
| 服务运维手册 | `服务运维手册.md` | 运维 | 服务对应关系、聚合入口、账号、启停与重启步骤、依赖顺序、开机自启、状态判读；PID 与模块资源统计边界 | README 部分覆盖，待补齐 |
| 测试计划与用例 | `测试计划与用例.md` | 测试 | 测试设备及版本基线、模块功能用例、安装升级 / 重启 / 离线用例、步骤、预期结果、通过标准与回归范围 | 待编写 |
| 联调与接口说明 | `联调与接口说明.md` | 测试、研发 | 跨模块 / 跨设备版本组合、拓扑、接口及参数、消息与话题、启动顺序、联调场景、异常表现 | 已有 Agent 调用示例，完整说明待编写 |
| 排障与 Bug 提报指南 | `排障与Bug提报指南.md` | 测试、运维 | 常见报错、排查命令、日志位置、诊断采集步骤、Bug 模板；要求附版本、时间、复现步骤及人工改动记录 | 已有检查脚本，指南待编写；日志规范待确认 |
| 备份与恢复手册 | `备份与恢复手册.md` | 运维 | 备份对象、命令、恢复条件和步骤、配置兼容限制、失败升级处理、恢复验收；明确没有自动回滚 | 待编写并实机验证 |
| 测试与交付验收报告 | `测试与交付验收报告.md` | 测试、运维、发布负责人 | 实测母盘、机型、build ID 和模块组合；用例结果、离线结论、遗留问题、证据及交接确认 | 每次发布生成，目前待提供 |

文档中的命令必须在对应目标设备上验证。未支持的单模块更新、自动回滚或尚未通过的离线验收，应写明限制；未完成项需在交付清单中明确标注，不能作为已具备能力交接。

### 配套软件与工具

| 交付项 | 内容与当前状态 |
| --- | --- |
| 软件与基础环境 | 已有总安装 `.run`；需配套明确母盘版本、适用机型和获取位置 |
| 发布说明与版本清单 | 已生成发布身份和工件清单，可用 `navi-version` 查询；待补齐模块声明版本、修复问题、兼容组合和已知限制 |
| 安装与运维手册 | 本文已有安装、环境和服务入口；待补齐安装验收、常见故障、配置备份及恢复步骤 |
| 测试说明 | 待补齐：模块用例、跨模块联调用例、前置条件、预期结果及回归范围 |
| 诊断工具 | 已有安装预检、Supervisor 和 `system_deployment/demo_supervisor_client.py` 接口示例；待提供一键采集版本、配置、服务状态及日志的诊断包工具 |
| 更新与恢复工具 | 待提供统一的单模块更新、关联模块联动更新及经验证的恢复入口 |

测试和运维的使用流程：确认母盘与版本 → 运行 `--pretest` → 安装 → 在 Supervisor 检查状态 → 按测试说明验证功能。进程启动成功不代表业务验收通过。

```bash
./navi_one_stop_installer-<version>.run -- --info
./navi_one_stop_installer-<version>.run -- --pretest
```

## Bug 修复与联调更新

当前总包按目标执行完整安装，**尚不支持通过参数选择单模块更新，也没有统一自动回滚入口**。已有模块独立安装包，但单独安装是否兼容当前依赖、配置和 Supervisor，需逐个验证。

| 场景 | 当前处理方式与后续要求 |
| --- | --- |
| 单模块 Bug 修复 | 研发提供修复模块包及适配的总包基线，确认依赖、配置和服务接入后在测试设备单独验证；未验证独立安装的模块通过重建总包测试。后续补齐统一选择模块、兼容检查和恢复能力 |
| 多模块 / 跨设备联调 | 固定 Orin、PICO 及相关模块的版本组合，明确接口、环境参数、停启顺序和测试步骤；当前通过更新统一配置、重建总包交付，后续支持按关联模块集合更新 |
| 正式交付运维 | 修复与联调通过后合入统一发布配置，输出经过回归的完整 `.run` 和发布说明，记录最终版本组合 |

Bug 反馈至少包含：`navi-version --json` 输出、复现步骤、预期与实际结果、发生时间、相关日志，以及是否做过单模块替换。研发随修复包给出影响范围、复测步骤和恢复办法；测试记录复测版本与结果。

单模块测试应覆盖原 Bug 和模块基本功能；涉及消息接口、共享依赖或环境变更时，需增加上下游及跨设备联调回归。更新前备份配置并保存原版本工件；恢复方案需验证依赖和配置兼容性，不能默认旧包可直接覆盖安装。
