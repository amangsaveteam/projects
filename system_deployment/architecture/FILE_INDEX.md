# File index and configuration map

This is the navigation map for future changes. The authoritative release inputs are under `release/`.

| Path | Role and current capability | Change here when |
|---|---|---|
| `build/build_release.sh` | Public build entry; passes `release/version.json`, `package-urls.json`, `supervisor.json` to the builder and writes to root `dist/`. | Changing build invocation or output directory. |
| `build/package_firmware.py` | Main builder: validates target config, downloads DEB/RUN/common packages, stages config/assets, generates target installers, supervisor files, manifests, checksums and final `.run`. | Changing module download, staging, validation, installer generation or package naming. |
| `build/build_release.py` | Python compatibility entry for the build pipeline. | Changing Python-level build dispatch. |
| `build/build_special_wa_t_jk2_v1_package.sh` | Special release wrapper using files in `release/`. | Changing the special WA-T-JK2 release. |
| `deploy/firmware.py` | Firmware-root validation boundary. | Adding firmware extraction or pre-deploy validation. |
| `deploy/install_*.py` | Runtime installation adapters for RUN payloads and platform-specific fixes. | Changing post-download installation behavior. |
| `deploy/release_state.py` | Records installing/success/failure release state and robot type. | Changing release lifecycle state. |
| `release/version.json` | Release version, branch/build identity and final `output_name` (`Middleware-2.0.0-1`). | Changing release identity or package name. |
| `release/package-urls.json` | Target definitions, module URLs/SHA, DEBs/RUNs, config files, environments, robot type rules and system config. | Changing delivered modules or target installation behavior. |
| `release/supervisor.json` | Managed/external/systemd module definitions, services, commands, ports and runtime environment. | Changing service supervision or startup order. |
| `release/special-*.json` | Special release variants. | Changing only the matching special release. |
| `assets/` | Payload resources copied into target installations: navigation, perception, audio, sensors, tools and PICO upperlimb. | Changing runtime files shipped by the firmware. |
| `common/configs` | Shared target/device definitions and robot type metadata. | Changing platform-wide target metadata. |
| `common/manifests` | Shared apt/package manifests. | Changing common dependency sets. |
| `common/templates` | Middleware/environment templates. | Changing generated runtime environment defaults. |
| `common/files` | Files installed by common packages. | Changing common package filesystem payload. |
| `config/config_map.yaml` | Maps robot types to hardware configuration sources and destinations. | Adding/changing hardware model mapping. |
| `config/*/hardware_body.yaml` | Default hardware configuration per robot family. | Changing default hardware parameters. |
| `packages/supervisor-agent` | Supervisor agent resources, module registration and secret initialization. | Changing agent behavior or registration. |
| `image/` | Golden Image build/prepare/verify scripts and target matrix. | Changing base-image preparation, not firmware packaging. |
| `docs/` | User, middleware and architecture documentation. | Updating operator instructions or architecture. |
| `tests/` | Build, deployment, configuration and architecture checks. | Adding regression coverage for behavior changes. |

## Implemented configuration capabilities

- Target-aware ORIN/PICO/RDK release selection.
- DEB and RUN module download with optional SHA256 validation and mirror fallback.
- Per-target `config_files`, `system_config`, `extra_debs`, `runs`, environment and robot-type conditions.
- Supervisor modules with managed, external and systemd integration, startup priority and service registration.
- Common dependency installation and system Python/CUDA contract validation.
- Middleware environment generation and robot-type pretest/identity checks.
- Payload checksums, release manifests, pretest mode and release-state tracking.
- Robot hardware defaults through `config/config_map.yaml`.
- Current final artifact name: `Middleware-2.0.0-1.run` (the builder currently emits `.run`, not `.firmware`).
