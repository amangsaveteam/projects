# 安装命令

## Common DEB：Orin Ubuntu 22.04 / Humble / arm64

```bash
cd /tmp
sudo dpkg -i navi_common_dep-2.0.0-release-humble-arm64.deb
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure --target orin-humble --robot-type WA1
sudo install_common_deps.sh
source /etc/naviai/Middleware.env
```

## Common DEB：Orin Ubuntu 24.04 / Jazzy / arm64

```bash
cd /tmp
sudo dpkg -i navi_common_dep-2.0.0-release-jazzy-arm64.deb
sudo python3 /usr/lib/navi-common-dep/deploy_common.py configure --target orin-jazzy --robot-type WA1
sudo install_common_deps.sh
source /etc/naviai/Middleware.env
```

## Common DEB：Pico Ubuntu 20.04 / Humble / amd64

```bash
cd /tmp
sudo dpkg -i navi_pico_common_dep-2.0.0-release-humble-amd64.deb
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure --target pico-humble --robot-type WA1
sudo install_pico_common_deps.sh
source /etc/nav01/Middleware.env
```

## Common DEB：Pico Ubuntu 24.04 / Jazzy / amd64

```bash
cd /tmp
sudo dpkg -i navi_pico_common_dep-2.0.0-release-jazzy-amd64.deb
sudo python3 /usr/lib/navi-pico-common-dep/deploy_common.py configure --target pico-jazzy --robot-type WA1
sudo configure_pico_jazzy_environment.sh
source /etc/nav01/Middleware.env
```

## Pico Humble 上肢额外 Common DEB

```bash
cd /tmp
sudo dpkg -i navi_pico_upperlimb_common_dep-2.0.0-release-humble-amd64.deb
sudo install_pico_upperlimb_common_deps.sh
```

## Run 包查看

```bash
cd /tmp
chmod +x <package>.run
./<package>.run -- --help
./<package>.run -- --version
./<package>.run -- --delivery
./<package>.run -- --packages
./<package>.run -- --info
./<package>.run -- --verify
```

## Run 包安装

```bash
cd /tmp
sudo ./<package>.run -- --robot-type WA1
./<package>.run -- --status
./<package>.run -- --verify-only
```

## Run 包卸载

```bash
cd /tmp
sudo ./<package>.run -- --uninstall
sudo ./<package>.run -- --uninstall --force
```
