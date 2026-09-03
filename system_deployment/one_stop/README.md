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
./navi_one_stop_installer-<version>.run --pretest
sudo ./navi_one_stop_installer-<version>.run -- --robot-type WA1
```

`--pretest` 是只读检查：自动识别 target，列出总包内 DEB 和各子运行包的期望版本、当前已装版本及
预计动作（`install`、`upgrade`、`reinstall` 或 `downgrade blocked`），不会停止服务或改动系统。

## 安装顺序

```text
1. 自动识别 OS、OS 版本和 CPU 架构（或使用 --target 指定）
2. 内嵌 system-config：写入 profile、Middleware、CycloneDDS 和设备身份
3. extra_debs：按 package-urls.json 的顺序 dpkg -i 并执行安装器
4. runs：按 package-urls.json 的顺序执行，并传入相同的 --robot-type
```

```text
Orin Humble：chassis → sensor → robot → audio → vision
Orin Jazzy ：chassis → sensor → robot → audio
Pico Humble：robot → upperlimb
Pico Jazzy ：upperlimb
RDK Jazzy  ：当前仅 common / sensor 依赖
```

各 target 的公共运行依赖应由对应母盘提供；总包不再下载或安装 common carrier。母盘制作矩阵见
[../golden_image/TARGET_MATRIX.md](../golden_image/TARGET_MATRIX.md)。
