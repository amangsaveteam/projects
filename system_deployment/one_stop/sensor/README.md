# WA-T 相机与离线依赖

硬件约定见 `wa-t-camera-profile.yaml`：头部为 USB `2-3` 的 RealSense，
双腕为 Gemini 305g GMSL。左右序列号/GMSL UID 尚未确认，不能用枚举顺序
自动猜测左右，也不能把旧 RealSense 腕部 USB 端口直接用于 Orbbec。
本目录的 YAML 是待接线确认的部署约定，尚未覆盖设备的 sensor 配置。

## 总包自动收集离线依赖

直接执行原来的命令：

```bash
bash system_deployment/one_stop/build_special_wa_t_jk2_v1_package.sh
```

首次构建会自动生成 `dist/common/orin/sensor/navi_orbbec_dep-2.9.3-humble-arm64.deb`，
随后嵌入总包；后续构建复用该文件。构建机可以是 x86 Ubuntu，无需在 Orin 上
安装驱动或手动拷贝依赖包。构建机需要 `apt-get`、`ubuntu-keyring`、`dpkg-deb`
和 `dpkg-scanpackages`（来自 `dpkg-dev`），首次构建需要访问 Ubuntu/ROS 镜像。

构建器使用独立的 Ubuntu 22.04 ARM64/Humble 软件源、签名校验、APT 索引及
空已安装状态解析递归依赖，不修改构建机软件源或已安装软件。设备安装只使用
随包的 file: APT 仓库；继承现有 Ubuntu 基础镜像包排除策略。
需要重新收集依赖时，移走缓存的离线 DEB 后再次运行总包命令。
`ORBBEC_OFFLINE_DEB` 仅作为可选的预构建依赖包路径覆盖，不是必需步骤。

ROS 驱动最低版本为 2.9.3，包含 `gemini_301_series.launch.py`。
GMSL 的载板/JetPack 内核驱动属于系统镜像，不能用 ROS 包替代。
离线依赖仅对 WA-T 安装，其他机型不安装。

## 腕部相机绑定策略

配置文件为 `wa-t-camera-profile.yaml`，支持两种模式：

```yaml
wrists:
  assignment_mode: manual
  left_gmsl_port: gmsl2-7
  right_gmsl_port: gmsl2-6
```

`manual` 模式用于量产装配端口固定的机型。部署人员只需修改两个
`*_gmsl_port`，不需要填写序列号。

如果端口每次枚举都可能变化，将 `assignment_mode` 改为 `auto`，并把两个
端口留空：

```yaml
wrists:
  assignment_mode: auto
  left_gmsl_port: ''
  right_gmsl_port: ''
```

自动模式要求枚举到**恰好两台** Gemini 305g，然后按 GMSL 端口字符串稳定排序：
排序后的第一台作为左腕，第二台作为右腕，并将最终映射写入
`/run/navi-sensor-host/orbbec-wrist-mapping.yaml`。这样不会依赖设备序列号，
但自动模式无法从硬件本身判断真实物理左右；首次部署应人工确认一次画面方向。

启动器流程为：执行 `ros2 run orbbec_camera list_devices_node`，过滤 Gemini 305g，
按上述策略解析左右，校验端口不重复，再分别启动两个
`gemini_301_series.launch.py` 实例。头部相机保持 USB `2-3` 配置。

