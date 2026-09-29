# 母盘文档

按目标设备打开对应文档，并依次执行文档中的脚本：

| target | 文档 | 系统 / ROS |
| --- | --- | --- |
| `orin-humble` | [Orin Humble](orin-humble/README.md) | Ubuntu 22.04 / Humble |
| `orin-jazzy` | [Orin Jazzy](orin-jazzy/README.md) | Ubuntu 24.04 / Jazzy |
| `pico-humble` | [Pico Humble](pico-humble/README.md) | Ubuntu 20.04 / 公司 ROS Humble |
| `pico-jazzy` | [Pico Jazzy](pico-jazzy/README.md) | Ubuntu 24.04 / Jazzy |

母盘只安装系统、ROS 和公共依赖。业务模块和系统配置由总安装包安装：

```bash
sudo ./navi_one_stop_installer-2.0.0.run -- \
  --target <target> --robot-type <实际机型>
```
