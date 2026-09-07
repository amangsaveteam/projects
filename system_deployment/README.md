# 系统交付

本目录只保留一个面向交付使用者的配置入口和构建入口：

- 配置：[`one_stop/package-urls.json`](one_stop/package-urls.json)
- 构建：[`one_stop/build_release.sh`](one_stop/build_release.sh)

`package-urls.json` 统一定义 Orin、Pico 和 RDK 各目标的远程安装包 URL、运行参数、离线依赖和
Supervisor 服务。不要再为单独平台或模块维护额外 manifest。

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
