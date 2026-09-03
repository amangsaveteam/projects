# Orin Jazzy 母盘使用

适用环境：Jetson AGX Orin、Ubuntu 24.04 arm64、JetPack 7.2.1 / L4T R39.2.1、CUDA 13.2、ROS 2 Jazzy。

## 制作母盘

```bash
cd system_deployment/golden_image

# 检查硬件、系统、架构、L4T 和 ROS。
./scripts/00-inventory.sh orin-jazzy

# 更新系统；如果提示内核/NVIDIA 更新，重启后再继续。
sudo ./scripts/10-prepare-os.sh orin-jazzy
sudo reboot

cd system_deployment/golden_image
sudo ./scripts/20-install-platform.sh orin-jazzy \
  --ros-apt-source-deb /path/to/ros2-apt-source_*_noble_all.deb

# 通过才可以制作镜像。
./scripts/30-verify-platform.sh orin-jazzy
```

## 脚本说明

| 脚本 | 用途 |
| --- | --- |
| `00-inventory.sh orin-jazzy` | 只读检查 Ubuntu 24.04、arm64、Jetson L4T 和 ROS。 |
| `10-prepare-os.sh orin-jazzy` | 更新系统。 |
| `20-install-platform.sh orin-jazzy` | 安装 JetPack、ROS Jazzy、CycloneDDS 和公共依赖。 |
| `30-verify-platform.sh orin-jazzy` | 检查 ROS 和公共依赖。 |

## 制作后安装业务包

母盘不要包含 chassis、sensor、audio、robot、vision 等业务包，也不要预写设备身份。克隆后执行：

```bash
sudo ./navi_one_stop_installer-2.0.0.run -- \
  --target orin-jazzy --robot-type <实际机型>
```
