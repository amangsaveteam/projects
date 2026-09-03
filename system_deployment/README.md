# System deployment

该目录是系统级交付的独立项目，负责两类不属于单一功能模块的内容：

- 全局 `common` 离线依赖包：Orin、Pico 与 RDK 的基础构建/运行环境；
- 通过声明式清单部署共享系统文件，并在需要时重新加载/启用 systemd 服务。

对于 Orin Ubuntu 22.04 / ROS 2 Humble，若交付方式改为母盘预装基础运行环境，请使用
[golden_image/orin-humble/README.md](golden_image/orin-humble/README.md)。该流程将 common 的
运行依赖沉入 JetPack 6.1 / Ubuntu 22.04 母盘；业务安装包不应再重复安装 common carrier。
其余 target 的系统、ROS 与依赖基线见 [golden_image/TARGET_MATRIX.md](golden_image/TARGET_MATRIX.md)。

模块自己的 `common_dep` 不迁入此项目。当前 `orin-sensor-common` 仍由
`configs/dependency-bundles/orin-sensor-common.json` 和 Sensor 流水线原样维护。

## 构建全局 common

在对应的原生目标机构建。当前 `orin-common` 面向 Ubuntu 24.04 arm64
（Orin）/ ROS 2 Jazzy，产物为 `navi_common_dep-2.0.0-release-jazzy-arm64.deb`。
该离线包包含 `libyaml-cpp0.8 (>= 0.8.0)` 和 Jazzy `spdlog 1.12.0` 的开发/运行时
离线依赖；模块自己的其他依赖仍应由各自的 `common_dep` 包交付。

```bash
python3 projects/system_deployment/common/build_common.py --config orin-common
python3 projects/system_deployment/common/build_common.py --config orin-common-humble
python3 projects/system_deployment/common/build_common.py --config pico-common
python3 projects/system_deployment/common/build_common.py --config rdk-common
```

构建机需要能从已配置的 Ubuntu 软件源取得 target 清单中的 payload。生成的 deb 可复制
到离线设备后安装：

```bash
sudo dpkg -i /tmp/navi_common_dep-2.0.0-release-jazzy-arm64.deb
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure \
  --target orin-jazzy --robot-type I3-S --robot-name robot-001
sudo /usr/sbin/install_common_deps.sh
```

若完整仓库的 `scripts/build/build_dependency_deb.py` 不在同级目录，三个 target 都使用
本项目内置的 carrier builder。它会下载并校验 target payload，构建内部包名为
`navi-common-dep`（Orin）或 `navi-pico-common-dep`（Pico）的离线包；离线设备先安装
carrier，再由安装器校验 SHA256 后安装内嵌 payload。

完整的传输、构建、离线部署与参数配置步骤见
[COMMON_USAGE.md](COMMON_USAGE.md)，按 Orin Jazzy、Orin Humble、Pico Humble 分开的
可执行手册见 [COMMON_TARGET_GUIDE.md](COMMON_TARGET_GUIDE.md)。旧版流程说明保留在
[PACKAGING_DEPLOYMENT.md](PACKAGING_DEPLOYMENT.md)。

只面向已收到 deb 的离线设备使用者，请使用不包含构建内容的
[COMMON_DEB_USAGE.md](COMMON_DEB_USAGE.md)。该文件也会随每个 common deb 安装到
`/usr/share/doc/navi-common-dep/USAGE.md` 或 `/usr/share/doc/navi-pico-common-dep/USAGE.md`。

安装 Orin 包后，Bash 登录 Shell 会加载 `/etc/profile.d/zj_humanoid.sh`。它会
设置 ROS 2 Domain、CycloneDDS、设备参数和 Docker Compose 参数，并从设备的
`/etc/zj_humanoid/device.env` 读取经过白名单校验的 `ROBOT_TYPE`、`ROBOT_NAME` 与
`ZJ_VERSION`。它根据设备与系统版本加载 ROS：Pico 使用 Humble、Orin Ubuntu 22.04
使用 Humble、Orin Ubuntu 24.04 使用 Jazzy、RDK OS V5.1.0 使用 Jazzy；`libyaml-cpp0.8` 安装在系统库路径，
不需要 `LD_LIBRARY_PATH`。

`ROBOT_TYPE` 是部署选择的必填项。profile 仅在它是受支持机型时才设置 ROS 网络与
`COMPOSE_PROFILES`；未设置或值无效时会清除这些部署变量并给出提示。可将其持久化在
设备 `.env`，或在当前 Shell 手动指定后重新加载 profile：

```bash
export ROBOT_TYPE=I3-S
source /etc/profile.d/zj_humanoid.sh
```

安装前可运行只读预检脚本；它不会安装或更新任何包：

```bash
./projects/system_deployment/common/check_orin_jazzy_arm64_env.sh --strict-target
```

Pico common 精简前可运行只读审计脚本。它按当前 Pico 清单报告包版本及 APT 的
`manual`/`auto` 标记，不会安装或删除任何包：

```bash
./projects/system_deployment/common/audit_pico_humble_dependencies.sh --strict-target
```

当前 `pico-common` 面向 Ubuntu 20.04 amd64 / ROS 2 Humble，产物为
`navi_pico_common_dep-2.0.0-release-humble-amd64.deb`，仅包含
`python3-yaml` 与 `python3-psutil`。ROS 和其他功能模块依赖必须由模块自己的
`common_dep` 包交付。

生成后可将 deb 复制到离线 Pico 并安装：

```bash
sudo dpkg -i /tmp/navi_pico_common_dep-2.0.0-release-humble-amd64.deb
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure \
  --target pico-humble --robot-type U2-D --robot-name pico-001
sudo /usr/sbin/install_pico_common_deps.sh
```

Pico 与 RDK 包同样会安装唯一的 `/etc/profile.d/zj_humanoid.sh`。该脚本按 Pico/Orin/RDK
设备和 Ubuntu 版本动态选择 ROS 2 发行版；不加载 ROS 1、ros1_bridge，也不设置
`ROS_MASTER_URI`、`ROS_IP` 或 `ROS_HOSTNAME`。

四个 common carrier 会在安装 carrier 的第一步写入统一模块环境文件：Orin 与 RDK 为
`/etc/naviai/Middleware.env`，Pico 为 `/etc/nav01/Middleware.env`。它们是 Debian
conffile，因此模块 deb 的 `postinst` 可在 payload 安装前安全 source；现场修改不会在
common 升级时被静默覆盖。该文件会加载当前平台的 ROS 2 setup（Orin 22.04 为 Humble、
Orin 24.04 与 RDK 为 Jazzy、Pico 为 Humble）。RDK 同时导出
`ROSDEP_OS_OVERRIDE=ubuntu:noble` 和 `ROS_OS_OVERRIDE=ubuntu:noble:noble`，供 SDK 的
rosdep/bloom DEB 构建流程使用。

原有 `scripts/build/build_common_deb.sh` 与 `build_pico_common_deb.sh` 是兼容入口，
会转发到这个项目。`scripts/build/build_dependency_deb.py --config orin-common`
也会从本项目读取配置；`orin-sensor-common` 不受影响。

## 部署系统文件

从 `system-files.manifest.example.json` 复制一份清单，并把要安装的文件放在本项目目录内。
先验证输出，再以 root 写入真实根目录：

```bash
python3 deploy_system_files.py --manifest my-system-files.json --root /tmp/navi-system-root --dry-run
sudo python3 deploy_system_files.py --manifest my-system-files.json
```

清单的文件操作采用同目录临时文件替换；服务激活失败时会还原已经覆盖的文件。只有 `--root /`
时允许执行 systemd 操作，临时根目录只用于验证文件布局。

## 生成可配置的 run 包

`build_run_package.py` 使用与 `one-stop-upgrade` 的 `version.json` 对齐的 JSONC 清单格式，
一个 run 包可同时包含 ORIN、PICO 与 RDK 的不同模块、资源和脚本。支持 `//` 注释与尾逗号；模板见
[run-package.manifest.example.json](run-package.manifest.example.json)。

从 [run-package.manifest.example.json](run-package.manifest.example.json) 复制清单，并将
本地配置和脚本放到清单同级目录。例如：

```text
my-release/
├── version.json
├── config/
└── scripts/
```

示例中的 SHA256 只是格式占位值；实际交付前必须替换为对应 module 的真实 SHA256。

`ORIN.modules`、`PICO.modules` 与 `RDK.modules` 按数组顺序安装 deb；卸载时会以相反顺序执行
`dpkg -r <name>`。模块的 `name` 因此是 Debian 包名，不能省略。`url` 支持 `http://`、
`https://` 与相对清单目录的 `local://`；`image` 可替代 `url`，安装时会执行 `docker pull`。

`resource` 是 deb 外的配置或文件：ORIN/RDK 使用 `url` 或 `local_path` 与目标 `path`，PICO 使用
`url` 或 `local_path` 与目标 `device_path`。安装时会复制到对应设备绝对路径；资源不会在卸载时自动删除，
以保护现场修改过的配置。

每个平台都可定义 `pre_install`、`post_install`、`pre_uninstall` 和 `post_uninstall` 钩子；
每一个钩子可配置一条 `cmd` 或一个相对于清单的 `path` 脚本。

需要开机自启的模块使用可选 `startup` 对象，而不需要手写 systemd unit、启动脚本或
`systemctl` 钩子。填写 `name`（不含 `.service`）、`command`、`script_directory`，并可选设置
`description`、`restart`（默认 `on-failure`）和 `start_on_install`（默认 `true`）。打包器会在
`script_directory` 生成启动脚本、在 `/etc/systemd/system/<name>.service` 生成服务；脚本只会
source 当前平台的统一 `Middleware.env`。卸载时会停止/禁用并删除该自动生成的服务与启动脚本。

`environment` 是可选的字符串键值对象，用于将模块变量写入同一个 `Middleware.env`，例如
`CHASSIS_HOST`。它不能覆盖 `MIDDLEWARE_*` 元数据。

安装会在任何安装钩子和第一个 `dpkg -i` 之前原子刷新统一环境文件，供后续所有模块 source：

- ORIN：`/etc/naviai/Middleware.env`
- PICO：`/etc/nav01/Middleware.env`
- RDK：`/etc/naviai/Middleware.env`

其中包含 `MIDDLEWARE_PLATFORM`、`MIDDLEWARE_VERSION`、`MIDDLEWARE_BUILD_TIME`、
`MIDDLEWARE_BRANCH_NAME`、`MIDDLEWARE_COMMIT_ID`、`MIDDLEWARE_SYS_ENV_VERSION` 和
`MIDDLEWARE_MODULES`。卸载只会删除由同一个 run 包版本写入的 `Middleware.env`，不会误删新版本。
模块启动脚本可直接加载它，例如 ORIN 使用 `. /etc/naviai/Middleware.env`，PICO 使用
`. /etc/nav01/Middleware.env`。

```bash
# 校验清单、路径和 JSONC 格式，不下载也不生成文件
python3 build_run_package.py --manifest my-release/version.json --dry-run

# 构建 run 包；为空的 build_time 会自动填入当前构建时间
python3 build_run_package.py --manifest my-release/version.json

# 默认产物目录：system_deployment 上一级目录的 dist/
# 如需覆盖，--output-dir 的相对路径仍相对于 system_deployment/ 解析
# python3 build_run_package.py --manifest my-release/version.json --output-dir ../my-dist

# 自动检测设备；无法检测或同时检测到两种设备时，显式指定平台
sudo ./dist/Middleware_*.run install --device ORIN
sudo ./dist/Middleware_*.run install --device PICO
sudo ./dist/Middleware_*.run install --device RDK

# 卸载该 run 包声明的 deb（逆序）并执行卸载钩子
sudo ./dist/Middleware_*.run uninstall --device PICO
```

运行包会把最终清单写入包内 `package-manifest.json`。包内 deb 的 conffile 会随着 `dpkg -i`
自动安装和升级，无需重复定义配置文件；运行包也会校验已配置的 SHA256。

### Delivery 交付接口

每个生成的 run 都会包含平台目录下的 `delivery.yaml`（`apiVersion: robot-studio/v1`、
`kind: Delivery`）、`packages.tsv`、`install.sh` 与根目录的 `payloads.sha256`。交付清单自动记录
版本、目标架构/OS/ROS、common carrier 要求、统一环境路径、内置 DEB、启动服务与 DDS 默认值。

```bash
# 以下交付查询命令建议使用 -- 分隔，避免与 makeself/兼容参数冲突
./dist/<delivery>.run -- --version
./dist/<delivery>.run -- --delivery
./dist/<delivery>.run -- --packages
./dist/<delivery>.run -- --info
./dist/<delivery>.run -- --verify

# 安装；--robot-type 会校验并写入 /etc/zj_humanoid/device.env
sudo ./dist/<delivery>.run -- --robot-type I3-S

# 已安装设备的检查：status 展示回执、DEB、环境与服务；verify-only 会在不安装时严格校验它们
./dist/<delivery>.run -- --status
./dist/<delivery>.run -- --verify-only

# 卸载先校验安装回执和内置 DEB 的精确版本；回执丢失时才允许 --force
sudo ./dist/<delivery>.run -- --uninstall
sudo ./dist/<delivery>.run -- --uninstall --force
```

`--help` 会显示当前支持的机器人型号。带 `startup` 的交付包会在成功安装后写入
`/var/lib/naviai/deliveries/` 安装回执；卸载会停止/禁用服务、校验模块版本后再移除模块。
