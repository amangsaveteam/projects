# System deployment

该目录是系统级交付的独立项目，负责两类不属于单一功能模块的内容：

- 全局 `common` 离线依赖包：Orin 的 `orin-common` 与 Pico 的 `pico-common`；
- 通过声明式清单部署共享系统文件，并在需要时重新加载/启用 systemd 服务。

模块自己的 `common_dep` 不迁入此项目。当前 `orin-sensor-common` 仍由
`configs/dependency-bundles/orin-sensor-common.json` 和 Sensor 流水线原样维护。

## 构建全局 common

在对应的原生目标机构建。当前 `orin-common` 面向 Ubuntu 24.04 arm64
（Orin）/ ROS 2 Jazzy，产物为 `navi_common_dep-2.0.0-release-jazzy-arm64.deb`。
该离线包目前仅包含 `libyaml-cpp0.8 (>= 0.8.0)`；模块自己的依赖仍应由各自的
`common_dep` 包交付。

```bash
python3 projects/system_deployment/common/build_common.py --config orin-common
python3 projects/system_deployment/common/build_common.py --config orin-common-humble
python3 projects/system_deployment/common/build_common.py --config pico-common
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
使用 Humble、Orin Ubuntu 24.04 使用 Jazzy；`libyaml-cpp0.8` 安装在系统库路径，
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

Pico 包同样会安装唯一的 `/etc/profile.d/zj_humanoid.sh`。该脚本按 Pico/Orin
设备和 Ubuntu 版本动态选择 ROS 2 发行版；不加载 ROS 1、ros1_bridge，也不设置
`ROS_MASTER_URI`、`ROS_IP` 或 `ROS_HOSTNAME`。

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
一个 run 包可同时包含 ORIN 与 PICO 的不同模块、资源和脚本。支持 `//` 注释与尾逗号；模板见
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

`ORIN.modules` 与 `PICO.modules` 按数组顺序安装 deb；卸载时会以相反顺序执行
`dpkg -r <name>`。模块的 `name` 因此是 Debian 包名，不能省略。`url` 支持 `http://`、
`https://` 与相对清单目录的 `local://`；`image` 可替代 `url`，安装时会执行 `docker pull`。

`resource` 是 deb 外的配置或文件：ORIN 使用 `url` 与目标 `path`，PICO 使用 `url` 或
`local_path` 与目标 `device_path`。安装时会复制到对应设备绝对路径；资源不会在卸载时自动删除，
以保护现场修改过的配置。

每个平台都可定义 `pre_install`、`post_install`、`pre_uninstall` 和 `post_uninstall` 钩子；
每一个钩子可配置一条 `cmd` 或一个相对于清单的 `path` 脚本。

安装会原子刷新统一环境文件，供后续所有模块 source：

- ORIN：`/etc/naviai/Middleware.env`
- PICO：`/etc/nav01/Middleware.env`

其中包含 `MIDDLEWARE_PLATFORM`、`MIDDLEWARE_VERSION`、`MIDDLEWARE_BUILD_TIME`、
`MIDDLEWARE_BRANCH_NAME`、`MIDDLEWARE_COMMIT_ID`、`MIDDLEWARE_SYS_ENV_VERSION` 和
`MIDDLEWARE_MODULES`。卸载只会删除由同一个 run 包版本写入的 `Middleware.env`，不会误删新版本。
模块启动脚本可直接加载它，例如 ORIN 使用 `. /etc/naviai/Middleware.env`，PICO 使用
`. /etc/nav01/Middleware.env`。

```bash
# 校验清单、路径和 JSONC 格式，不下载也不生成文件
python3 build_run_package.py --manifest my-release/version.json --dry-run

# 构建 run 包；为空的 build_time 会自动填入当前构建时间
python3 build_run_package.py --manifest my-release/version.json --output-dir dist

# 自动检测设备；无法检测或同时检测到两种设备时，显式指定平台
sudo ./dist/Middleware_*.run install --device ORIN
sudo ./dist/Middleware_*.run install --device PICO

# 卸载该 run 包声明的 deb（逆序）并执行卸载钩子
sudo ./dist/Middleware_*.run uninstall --device PICO
```

运行包会把最终清单写入包内 `package-manifest.json`。包内 deb 的 conffile 会随着 `dpkg -i`
自动安装和升级，无需重复定义配置文件；运行包也会校验已配置的 SHA256。
