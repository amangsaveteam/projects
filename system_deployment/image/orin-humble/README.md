# Orin Humble 母盘使用

适用环境：Jetson Orin、Ubuntu 22.04 arm64、JetPack 6.1 / L4T R36.4、ROS 2 Humble。

## 制作母盘

将整个 `system_deployment` 目录复制到已刷写的 Orin 后执行：

```bash
cd system_deployment/golden_image/orin-humble

# 检查硬件、系统、架构和 L4T；输出留作母盘记录。
sudo ./scripts/00-collect-hardware.sh --output /tmp/orin-hardware-inventory.txt
cat /tmp/orin-hardware-inventory.txt

# 更新系统。完成后必须重启。
sudo ./scripts/10-prepare-os.sh
sudo reboot

# 重启并重新登录后继续。
cd system_deployment/golden_image/orin-humble
sudo ./scripts/20-install-jetpack-components.sh
sudo ./scripts/30-install-ros-humble.sh \
  --ros-apt-source-deb /path/to/ros2-apt-source_*_jammy_all.deb
sudo ./scripts/40-install-platform-dependencies.sh

# 通过才可以制作镜像。
sudo ./scripts/60-verify-golden-image.sh
```

`ros2-apt-source` deb 由发布人员提前下载并和母盘一起保存；不要在脚本中使用未记录版本的 `latest` 文件。

## 脚本说明

| 脚本 | 用途 |
| --- | --- |
| `00-collect-hardware.sh` | 只读采集硬件、磁盘、网络、OS、L4T 信息。 |
| `10-prepare-os.sh` | 更新 Ubuntu 和 NVIDIA 软件包，随后要求重启。 |
| `20-install-jetpack-components.sh` | 安装 `nvidia-jetpack`。 |
| `30-install-ros-humble.sh` | 安装 ROS Humble 和 CycloneDDS RMW。 |
| `40-install-platform-dependencies.sh` | 安装母盘公共 C++/Python/ROS 依赖。 |
| `60-verify-golden-image.sh` | 检查上述母盘依赖是否已经安装。 |

## 制作后安装业务包

母盘不要安装 common deb，也不要预写机器人机型、名称、DDS 配置或业务模块。将母盘克隆到设备后，由总安装包统一写入系统配置并安装业务模块：

```bash
sudo ./navi_one_stop_installer-2.0.0.run -- \
  --target orin-humble --robot-type <实际机型>
```
