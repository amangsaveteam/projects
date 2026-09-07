# 通用 Supervisor 模块栈

`build_supervised_stack.py` 从一个 stack manifest 构建设备级部署包。每个模块可使用
`deb` 或 `run` URL，选择构建时内嵌（`delivery: embed`）或目标机下载
（`delivery: fetch`，必须提供 SHA256）。

模块的 `supervisor.mode` 为：

- `managed`：根据 `command`、环境、工作目录和端口自动生成 `launch.sh`、独立
  `supervisord.conf`、systemd 服务及 Agent 的 `modules.d` 条目。
- `external`：安装器本身已经拥有 Supervisor。构建器只安装、执行 hook，并注册其
  已知 XML-RPC 地址。

构建当前 Orin Humble 配置：

```bash
python3 system_deployment/supervised_stack/build_supervised_stack.py \
  --manifest system_deployment/supervised_stack/configs/orin-humble.json \
  --output-dir dist
```

构建 Pico Humble I2（Agent + 集成式下肢/上肢）配置：

```bash
python3 system_deployment/supervised_stack/build_supervised_stack.py \
  --manifest system_deployment/supervised_stack/configs/pico-humble.json \
  --output-dir dist
```

该清单生成 `navi_pico_lowerlimb_installer-2.0.1-release-humble-amd64.run`。

`target` 可独立指定模块配置目录、运行时目录、日志目录和 Agent 密码文件。因此 Pico 的
`/etc/nav01` 与 Orin 的 `/etc/naviai` 不会混用。每个设备的 Agent 仍监听 `0.0.0.0:9080`；
模块 Supervisor 仅绑定其配置中的固定机器人内网地址。

Pico I2 总包内置 `/etc/nav01/Middleware.env`、设备 profile、CycloneDDS 配置和
`device.env`。安装时根据 `--robot-type` 写入机器人型号，并固定 ROS domain 为 `72`。
I2 下肢包拥有 RTIPC 1.4.3；上肢作为独立的 `navi-pico-upperlimb.service` 启动，
只安装 logging 依赖而不安装或降级 RTIPC。该服务在下肢服务之后启动，并在下肢重启时
随之停止/重启。上肢 I2 配置仅在私有 mount namespace 中映射到 runtime 所读路径，
不会写入宿主机的 `/var/opt/hardware_body.yaml`。

所有 HTTP URL 都应提供 `sha256`。`fetch` 模式会在目标机写入
`/var/cache/naviai/supervised-stack/`，下载完成后才替换缓存文件。

## 统一 Pico / Orin 安装包

`configs/unified-humble.json` 将 Pico 与 Orin 两个既有清单构建并内嵌到同一个
run 包。安装器优先读取 `/etc/zj_humanoid/device.env` 的 `ZJ_DEVICE` 和
`ROBOT_TYPE`；未配置设备类型时，按 Pico 的 Ubuntu 20.04 amd64 或 Orin 的
Ubuntu 22.04 arm64 自动识别。环境变量 `ROBOT_TYPE` 可覆盖设备文件，`--robot-type`
拥有最高优先级。

```bash
# 一个入口：自动重建 Pico、Orin 和统一包
./system_deployment/supervised_stack/build_humble_release.sh unified

# 仅重建一个平台，适合迭代对应 configs/*.json 中的模块版本或新增模块
./system_deployment/supervised_stack/build_humble_release.sh pico
./system_deployment/supervised_stack/build_humble_release.sh orin

# 目标机自动识别平台和已配置机型
sudo ./dist/navi_unified_humble_stack-2.0.1-release.run --

# 新设备可显式指定；--pretest 只做校验，--uninstall 删除本包生成的服务与注册
sudo ./dist/navi_unified_humble_stack-2.0.1-release.run -- --device PICO --robot-type I2
sudo ./dist/navi_unified_humble_stack-2.0.1-release.run -- --pretest
sudo ./dist/navi_unified_humble_stack-2.0.1-release.run -- --uninstall
```

`--uninstall` 会停止、禁用并删除由该包生成的 managed Supervisor systemd 服务、
其启动脚本和 Agent 注册文件；不会猜测或 purge 外部 run 包/DEB 安装的业务依赖，避免
误删现场共享运行时。需要删除外部模块时，应使用该模块自身的卸载接口。

当前 Orin Humble 清单已填写 Sensor、Robot、Audio 的制品 URL，并使用
`delivery: embed`。网络可达制品服务器时，直接执行通用构建命令即可自动下载、校验并
封入总 `.run`；若通过历史兼容入口构建，则它会使用 `dist/` 中同一 SHA256 的已下载缓存。
