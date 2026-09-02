# 总安装包构建

```bash
python3 system_deployment/one_stop/build_one_stop_package.py \
  --version system_deployment/one_stop/version.json \
  --urls system_deployment/one_stop/package-urls.json \
  --output-dir dist/one-stop
```

```bash
./navi_one_stop_installer-<version>.run -- --list-targets
./navi_one_stop_installer-<version>.run -- --info
sudo ./navi_one_stop_installer-<version>.run -- --robot-type WA1
```

## 安装顺序

```text
1. 自动识别 OS、OS 版本和 CPU 架构（或使用 --target 指定）
2. common DEB：dpkg -i
3. deploy_common.py configure --target <target> --robot-type <机型>
4. common payload 安装器
5. extra_debs：按 package-urls.json 的顺序 dpkg -i 并执行安装器
6. runs：按 package-urls.json 的顺序执行，并传入相同的 --robot-type
```

```text
Orin Humble：chassis → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio
Pico Humble：robot → upperlimb
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```
