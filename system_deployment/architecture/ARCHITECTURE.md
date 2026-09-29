# system_deployment 目录职责

- `build/`：构建流程、模块下载、固件 staging 和发布产物生成。
- `deploy/`：固件解包、设备识别、配置、环境和服务安装。
- `release/`：版本、模块来源和 Supervisor 发布输入；不放构建产物。
- `assets/`：随固件交付的目标配置和运行资源。
- `common/`：跨目标共享模板、清单和基础安装逻辑。
- `targets/`：目标平台差异（后续迁移 ORIN/PICO 专属逻辑）。
- `packages/`：可独立交付或安装的功能包。
- `tests/`：构建、部署和安装验收测试。
- `one_stop/`：已删除；历史入口已迁移到规范目录。

构建输入固定来自 `release/`，构建输出写入仓库外的 `dist/`；固件内部 staging 目录由 `build/` 管理，部署代码不参与模块下载。
- `image/`：Golden Image 制作与验收脚本；不参与 firmware 模块下载和 Makeself 构建。
- 历史 `supervised_stack/`、`patches/` 已归档并删除。

## one_stop 精简结论

`one_stop/` 原本同时存放构建、部署、发布配置、资源和文档，职责重复且边界不清。现已完成迁移：资源进入 `assets/`，构建脚本进入 `build/`，部署脚本进入 `deploy/`，发布配置进入 `release/`，文档进入 `docs/`。`one_stop/` 已删除。

历史 `supervised_stack/` 和 `patches/` 已归档到外部临时归档，不再位于源码树。
