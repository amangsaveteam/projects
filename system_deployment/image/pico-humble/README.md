# Pico Humble 母盘使用

适用环境：Pico x86_64、Ubuntu 20.04 amd64、公司认证的 ROS 2 Humble/Focal 镜像。

开始前必须确认镜像已有：

```bash
test -r /opt/ros/humble/setup.bash
dpkg-query -W ros-humble-rmw-cyclonedds-cpp
```

不要在 Ubuntu 20.04 上直接使用面向 Ubuntu 22.04 的官方 ROS Humble 源。

## 制作母盘

```bash
cd system_deployment/golden_image

# 检查系统、架构和已有 ROS。
./scripts/00-inventory.sh pico-humble

# 更新系统；需要时重启。
sudo ./scripts/10-prepare-os.sh pico-humble
sudo reboot

cd system_deployment/golden_image
sudo ./scripts/20-install-platform.sh pico-humble

# 通过才可以制作镜像。
./scripts/30-verify-platform.sh pico-humble
```

## 脚本说明

| 脚本 | 用途 |
| --- | --- |
| `00-inventory.sh pico-humble` | 只读检查 Ubuntu 20.04、amd64 和 ROS。 |
| `10-prepare-os.sh pico-humble` | 更新系统。 |
| `20-install-platform.sh pico-humble` | 检查内部 ROS Humble/CycloneDDS，安装 `python3-yaml` 与 `python3-psutil`。 |
| `30-verify-platform.sh pico-humble` | 检查 ROS、CycloneDDS 和 Pico 公共依赖。 |

## 制作后安装业务包

母盘不要包含 upperlimb、robot 等业务包，也不要预写机器人机型。克隆后执行：

```bash
sudo ./navi_one_stop_installer-2.0.0.run -- \
  --target pico-humble --robot-type <实际机型>
```
