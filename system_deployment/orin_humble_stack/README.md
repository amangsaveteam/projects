# Orin Humble 四模块部署包（兼容入口）

该目录保留历史构建命令与 Audio/Sensor 厂商安装适配器。模块定义、Supervisor 启动脚本和
systemd 单元已迁移至 [`../supervised_stack/configs/orin-humble.json`](../supervised_stack/configs/orin-humble.json)。

优先使用通用构建器：

```bash
python3 system_deployment/supervised_stack/build_supervised_stack.py \
  --manifest system_deployment/supervised_stack/configs/orin-humble.json \
  --output-dir dist
```

历史入口仍保持可用。将 URL 下载得到的 `sensor`、`robot`、`audio` 三个 `.run` 放入仓库根目录的
`dist/`，并先构建本仓库的 `chassis` 和 Orin Supervisor Agent：

```bash
python3 system_deployment/build_run_package.py --manifest packages/chassis/chassis-run.manifest.json --output-dir output
python3 system_deployment/build_run_package.py --manifest packages/supervisor-agent/supervisor-agent-orin-run.manifest.json --output-dir output
python3 system_deployment/orin_humble_stack/build_orin_humble_stack.py
```

生成的 `dist/navi_orin_humble_modules_supervisor-2.0.0-release-humble-arm64.run` 内嵌 Sensor、Robot、
Chassis、Audio 和聚合 Agent。目标机器已完成 Orin Humble common 初始化后执行：

```bash
sudo ./navi_orin_humble_modules_supervisor-2.0.0-release-humble-arm64.run -- --robot-type WA1
```

模块的 Supervisor XML-RPC 只绑定 Orin 内网地址 `192.168.217.100`：Sensor `19001`、Robot `19002`、
Audio `19003`、Chassis `19004`。聚合页面无 Token，监听 `0.0.0.0:9080`，因此局域网可访问
`http://<Orin-LAN-IP>:9080`。
