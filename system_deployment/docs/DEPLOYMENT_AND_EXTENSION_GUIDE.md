# Middleware 部署与扩展规范

这份文档是交付包的操作入口。发布输入只有 `release/` 下的三个文件：

```text
version.json          版本、分支和输出文件名
package-urls.json     target、DEB/RUN、配置文件和环境
supervisor.json       Supervisor/Agent 模块、端口和启动顺序
```

## 一、从构建到设备安装

在仓库根目录执行配置预检和构建：

```bash
python3 system_deployment/architecture/check_architecture.py
./system_deployment/build/build_release.sh --dry-run
NO_PROXY=10.51.33.211 no_proxy=10.51.33.211 \
  ./system_deployment/build/build_release.sh
```

产物写入仓库外的 `dist/`，当前为 `Middleware-2.0.0-1.run`。将它复制到目标机后：

```bash
chmod +x Middleware-2.0.0-1.run
sudo ./Middleware-2.0.0-1.run -- --pretest
sudo ./Middleware-2.0.0-1.run -- --robot-type <真实机型>
```

预检必须通过 Ubuntu/架构、target、模块下载和设备身份检查。设备已有正确
`/etc/zj_humanoid/device.env` 时仍建议显式传入机型；更改已有机型必须追加
`--confirm-robot-type-change`。安装顺序固定为：身份和 manifest 校验、Common DEB、
模块 DEB/RUN、随包配置、环境与硬件默认值、Agent/Supervisor 注册、服务启动和
release-state 写入。不要跳过配置脚本直接启动模块。

安装后检查：

```bash
exec bash
env | grep -E '^(ROS_|RMW_|CYCLONEDDS_URI|ROBOT_TYPE|ZJ_)'
systemctl status zj-humanoid-orin-supervisor-agent.service  # ORIN
systemctl status zj-humanoid-pico-supervisor-agent.service  # PICO
find /etc/naviai/supervisor-agent/modules.d -type f -maxdepth 1  # ORIN
systemctl list-units 'zj-humanoid-*-supervisor.service'
journalctl -u zj-humanoid-orin-supervisor-agent.service -b
```

PICO 的根目录和 Agent 配置目录使用 `/etc/nav01`；ORIN 使用 `/etc/naviai`。
硬件默认值来自 `config/config_map.yaml`，生成到 `/var/opt/hardware_body.yaml`，升级时
应保留用户字段。失败时保留安装工作目录、日志和 release-state，使用安装器的重装/回滚
流程处理，不要手工删除旧版本目录。

## 二、添加 DEB

只编辑对应 target 的 `release/package-urls.json`：

```json
"extra_debs": [{
  "name": "example-runtime",
  "url": "https://artifacts.example/example-runtime_1.2.3_arm64.deb",
  "sha256": "<64位 sha256>",
  "installers": [],
  "environment": {"EXAMPLE_MODE": "production"},
  "robot_types": ["WA1"]
}]
```

`name` 在该 target 内唯一；`url` 是构建机下载地址；填写 `sha256`；没有额外安装器时用
`[]`，需要构建器自动探测时用 `["auto"]`。`environment` 是安装环境，运行时环境放在
`runtime.environment`；`robot_types` 省略表示全部机型。需要分组或等待依赖时使用
`install_group`、`wait_packages` 和 `skip_if_installed`。不要把 deb 放进源码树，产物只能在
`dist/` 或外部制品库。

## 三、添加 RUN

在同一 target 的 `runs` 增加唯一 `name`：

```json
{"name":"example-service","url":"https://artifacts.example/example.run",
 "sha256":"<64位 sha256>","arguments":["--robot-type","{robot_type}"],
 "start_policy":"install","robot_types":["WA1"]}
```

`arguments` 中的 `{robot_type}` 由安装器替换；冲突包放入 `remove_packages`；安装环境用
`environment`，服务运行环境用 `runtime`。RUN 的执行由构建器生成，禁止手工改生成的
installer。

## 四、配置 Supervisor 与 Agent

在 `release/supervisor.json` 的目标中新增 `supervisor_modules` 项。`id` 和 `port` 必须在
目标内唯一，`mode` 只能是 `managed`、`external` 或 `systemd`：

```json
{"id":"example","package":"example-service","mode":"managed","port":19020,
 "working_directory":"/opt/example","source_files":["/etc/naviai/Middleware.env"],
 "command":"/opt/example/start.sh","startup_priority":70}
```

`managed` 由总包生成和托管；`external` 只注册已有服务并可用 `restart_service`、
`native_rpc_config` 接入；`systemd` 复用包提供的 `systemd_service`。需要等待或停用服务时
使用 `after_services`、`disable_services`。不要新增未登记的启动脚本或第二份 Supervisor 配置。

Agent 基础配置来自 `packages/supervisor-agent/resources/`，模块扩展文件放在目标机的
`modules.d/`（ORIN `/etc/naviai/...`，PICO `/etc/nav01/...`）。文件只描述 endpoint 等
注册信息，Agent 启动时合并它们。RPC 用户固定为 `agent`，密码由初始化脚本写入配置的
`supervisor-rpc.password`；原生 Supervisor 配置必须引用同一个凭据文件。Agent HTTP 页面
无认证，只能暴露在受信任 LAN。

## 五、提交前强制检查

```bash
python3 system_deployment/architecture/check_architecture.py
python3 -m py_compile system_deployment/build/package_firmware.py
./system_deployment/build/build_release.sh --dry-run
```

任何新增 DEB、RUN、Supervisor 模块、Agent 注册或部署顺序变更，都必须同步更新本文件或
相关文档、架构文件索引，并在变更记录中写明目的、兼容性、验证和回滚方式。后续开发人员
应以 `release/` 配置和本规范为唯一来源；设备上的生成文件只用于诊断，不能作为持久修改入口。
