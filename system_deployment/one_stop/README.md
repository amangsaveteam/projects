# 总安装包构建

```bash
./system_deployment/one_stop/build_release.sh
```

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
3. extra_debs：按 package-urls.json 的顺序 dpkg -i 并执行安装器
4. runs：按 package-urls.json 的顺序执行，并传入相同的 --robot-type
```

```text
Orin Humble：chassis → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio → vision
Pico Humble：robot → upperlimb
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```

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
Vision 的 `*_dep.deb` postinst 自行准备版本化 venv/runtime；总包不会再重复调用其
`install_vision_deps.sh`，避免无参数调用导致安装中断。

各 target 的公共运行依赖应由对应母盘提供；总包不再下载或安装 common carrier。母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。
