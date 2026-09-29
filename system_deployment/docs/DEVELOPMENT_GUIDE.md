# Middleware 交付开发文档

本文说明如何向当前交付包新增 DEB、RUN、环境变量、配置文件、Supervisor 服务和聚合 Agent 注册。所有发布输入都在 `system_deployment/release/`，资源文件在 `system_deployment/assets/`。

## 1. 修改位置

```text
release/version.json          版本、分发包名
release/package-urls.json     目标、DEB/RUN、配置文件、环境变量
release/supervisor.json       Supervisor 服务和 Agent 聚合注册
assets/                       随包交付的配置、脚本、启动资源
common/                       跨目标模板、清单和公共文件
packages/supervisor-agent/    Agent 程序和模块注册资源
config/                       机器人硬件型号配置
```

不要在代码里新增第二份 `version.json`、`package-urls.json` 或 `supervisor.json`。

## 2. 新增 DEB

在 `release/package-urls.json` 对应 target 的 `extra_debs` 增加条目：

```json
{
  "name": "example-runtime",
  "url": "https://artifacts.example/example-runtime_1.2.3_arm64.deb",
  "sha256": "<64 位 SHA256>",
  "installers": [],
  "environment": {
    "EXAMPLE_MODE": "production"
  },
  "robot_types": ["WA1", "WA2"]
}
```

字段说明：

- `name`：target 内唯一的模块名。
- `url`：构建机下载地址。
- `sha256`：建议填写；构建器会校验下载内容。
- `installers`：DEB 内需要额外执行的安装器路径；没有则使用 `[]`，自动探测使用 `["auto"]`。
- `environment`：安装 DEB 时注入的环境变量。
- `runtime`：生成模块启动环境，可包含 `source_files`、`environment`、`unset_environment`。
- `robot_types`：只对指定机型安装；省略表示所有机型。
- `install_group`：需要和其他 DEB 按组安装时使用。

安装顺序由 `extra_debs` 顺序和 `install_group` 决定。需要等待其他包时使用 `wait_packages`；已安装可跳过时使用 `skip_if_installed`。

## 3. 新增 RUN

在对应 target 的 `runs` 增加：

```json
{
  "name": "example-service",
  "url": "https://artifacts.example/example-service_2.0.0.run",
  "sha256": "<64 位 SHA256>",
  "arguments": ["--robot-type", "{robot_type}"],
  "robot_types": ["WA1", "WA2"],
  "start_policy": "install",
  "environment": {
    "EXAMPLE_CONFIG": "/home/naviai/navi_project/config/example.yaml"
  }
}
```

常用字段：

- `arguments`：RUN 执行参数，`{robot_type}` 会替换为安装时机型。
- `start_policy`：控制安装后是否启动。
- `remove_packages`：安装前移除冲突 DEB。
- `environment`：执行 RUN 时使用的安装环境。
- `runtime`：服务运行时的环境设置。
- `robot_types`：限制机型。

RUN 和 DEB 的实际安装行为由构建器生成到 target installer 中，不要手工修改生成的安装脚本。

## 4. 新增随包配置文件

在 target 的 `config_files` 增加：

```json
{
  "source": "system_deployment/assets/orin-humble/example",
  "destination": "/home/naviai/navi_project/config/example",
  "owner": "naviai",
  "group": "naviai",
  "overwrite": false,
  "robot_types": ["WA1"]
}
```

规则：

- `source` 必须位于仓库内，推荐放在 `assets/<platform>/`。
- `destination` 必须是绝对目录。
- `overwrite: false` 适合用户可修改的配置，升级时不会覆盖。
- 文件内容需要随包交付时，提交到 `assets/`，不要直接写入生成脚本。

硬件默认配置不放在 `assets/`，应放在 `config/<robot-family>/hardware_body.yaml`，并在 `config/config_map.yaml` 注册。

## 5. 环境变量

安装环境在 DEB/RUN 条目的 `environment` 配置；运行环境在 `runtime.environment` 配置：

```json
"runtime": {
  "source_files": ["/opt/ros/humble/setup.bash"],
  "unset_environment": ["PYTHONPATH"],
  "environment": {
    "ROS_DOMAIN_ID": "10",
    "RMW_IMPLEMENTATION": "rmw_cyclonedds_cpp"
  }
}
```

环境变量配置原则：

- 固定的 Middleware 环境放 `common/templates/Middleware.*.env`。
- target 或模块专属变量放 `package-urls.json` / `supervisor.json`。
- 用户运行时参数放设备上的 `.env` 或用户配置文件，不写死进安装器。
- 变量名必须符合 `[A-Za-z_][A-Za-z0-9_]*`。

## 6. Supervisor 模块

在 `release/supervisor.json` 的目标下增加 `supervisor_modules`：

```json
{
  "id": "example",
  "package": "example-service",
  "mode": "managed",
  "service_name": "zj-humanoid-orin-example-supervisor.service",
  "command": ["/usr/local/bin/example", "--config", "/etc/naviai/example.yaml"],
  "working_directory": "/home/naviai/navi_project",
  "source_files": ["/opt/ros/humble/setup.bash"],
  "environment": {
    "ROS_DOMAIN_ID": "10"
  },
  "startup_priority": 30,
  "robot_types": ["WA1", "WA2"],
  "port": 19010
}
```

模式：

- `managed`：总包生成 supervisord/systemd 启动配置。
- `external`：模块已有 Supervisor，总包只注册 Agent 端点。
- `systemd`：复用模块包提供的 systemd 服务。

必须保证：

- `id` 唯一。
- Agent 端口唯一。
- `service_name` 符合 systemd 命名规则。
- 新模块必须进入 `supervisor_modules`，不能新增未注册的直接启动路径。

## 7. 聚合 Agent 配置

Agent 聚合入口由 `packages/supervisor-agent/resources/` 提供。通常只需在 `supervisor.json` 注册模块，构建器会生成：

```text
/etc/naviai/supervisor-agent/modules.d/<module>.json
/etc/naviai/supervisor-agent/modules.json
/etc/systemd/system/zj-humanoid-<platform>-supervisor-agent.service
```

模块状态可通过 Agent 页面或 RPC 查看。模块配置变更应修改 `supervisor.json`，不要直接修改设备上的 `/run` 文件。

## 8. 开发验证

```bash
python3 system_deployment/architecture/check_architecture.py
python3 -m py_compile system_deployment/build/package_firmware.py
./system_deployment/build/build_release.sh --dry-run
```

正式构建输出当前为：

```text
dist/Middleware-2.0.0-1.run
```

## 10. 生成 ROS 2 模块接口文档

Python API 文档不等于模块运行接口。模块的 ROS 2 Topic、Service、Action 必须在目标设备上从运行时 graph 采集：

```bash
source /opt/ros/humble/setup.bash   # 或 jazzy
source /etc/profile.d/zj_humanoid.sh
python3 system_deployment/tools/generate_ros2_api_docs.py --output-dir /tmp/middleware-ros2-api
```

生成：

```text
/tmp/middleware-ros2-api/ROS2_API.md
/tmp/middleware-ros2-api/ros2-api.json
```

采集内容包括节点、Topic 类型、Service 类型和 Action 类型。需要更详细的 QoS、发布者/订阅者和服务端/客户端关系时，再对 JSON 中的名称执行：

```bash
ros2 topic info -v /topic/name
ros2 service type /service/name
ros2 action info /action/name
ros2 node info /node/name
```

采集前必须确保模块已经启动、ROS_DOMAIN_ID 正确、DDS 配置已经加载；否则只能得到不完整的 graph。
