# Pico Jazzy 母盘使用

适用环境：Pico x86_64、Ubuntu 24.04 amd64、ROS 2 Jazzy。

## 制作母盘

```bash
cd system_deployment/golden_image

# 检查系统、架构和 ROS。
./scripts/00-inventory.sh pico-jazzy

# 更新系统；需要时重启。
sudo ./scripts/10-prepare-os.sh pico-jazzy
sudo reboot

cd system_deployment/golden_image
sudo ./scripts/20-install-platform.sh pico-jazzy \
  --ros-apt-source-deb /path/to/ros2-apt-source_*_noble_all.deb

# 通过才可以制作镜像。
./scripts/30-verify-platform.sh pico-jazzy
```

## 脚本说明

| 脚本 | 用途 |
| --- | --- |
| `00-inventory.sh pico-jazzy` | 只读检查 Ubuntu 24.04、amd64 和 ROS。 |
| `10-prepare-os.sh pico-jazzy` | 更新系统。 |
| `20-install-platform.sh pico-jazzy` | 安装 ROS Jazzy 与 CycloneDDS RMW。 |
| `30-verify-platform.sh pico-jazzy` | 检查 ROS 和 CycloneDDS RMW。 |

## 制作后安装业务包

母盘不要包含 upperlimb、robot 等业务包，也不要预写机器人机型。克隆后执行：

```bash
sudo ./navi_one_stop_installer-2.0.0.run -- \
  --target pico-jazzy --robot-type <实际机型>
```
