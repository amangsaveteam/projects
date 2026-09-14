# 系统交付

本目录按分工维护两份配置，保留统一构建入口：

- 配置：[`one_stop/package-urls.json`](one_stop/package-urls.json)
- 服务配置：`one_stop/supervisor.json`
- 构建：[`one_stop/build_release.sh`](one_stop/build_release.sh)

`package-urls.json` 定义各目标的安装包 URL、安装参数、依赖与开发者环境变量；
`supervisor.json` 定义 Supervisor 服务及安装整合策略。构建时合并两份配置。

构建总安装包：

```bash
cd /home/huyingkai/projects
NO_PROXY=10.51.33.211 no_proxy=10.51.33.211 \
./system_deployment/one_stop/build_release.sh
```

产物固定为：

```text
dist/navi_one_stop_installer-<version>.run
```

安装时会按操作系统版本与 CPU 架构自动识别目标；已配置设备会从 `/etc/zj_humanoid/device.env` 读取 `ROBOT_TYPE`。裸机仅在缺少该值时需要传入 `--robot-type`。
