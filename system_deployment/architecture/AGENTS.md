# system_deployment architecture guardian

This file is the persistent contract for the architecture guardian agent.

## Directory boundaries

- `build/`: release input validation, module downloads, staging, manifests and Makeself packaging. It must not contain device deployment logic.
- `deploy/`: installation, device identity, runtime configuration, service and release-state handling. It must not download release modules.
- `release/`: the single source of truth for version, output name, module URLs, target configuration and supervisor definitions.
- `assets/`: files copied into a firmware payload at build time.
- `common/`: shared manifests, templates, package builders and deployment helpers.
- `config/`: robot hardware mappings and default hardware configuration.
- `packages/`: independently delivered packages such as supervisor-agent.
- `image/`: Golden Image preparation and verification; independent of firmware packaging.
- `docs/`: user and architecture documentation.
- `tests/`: behavior and architecture checks.

## Release and Agent contracts

- `release/package-urls.json` is the only owner of target delivery inputs. Add
  DEB/RUN entries under the target's `extra_debs`/`runs`; do not add download
  or install lists to Python, shell, `assets/`, or Agent files.
- `release/supervisor.json` is the only owner of Supervisor module ownership,
  ports, startup order, commands, and systemd/external integration. Every
  module id and port is unique within a target and every module uses one of
  `managed`, `external`, or `systemd` mode.
- The Agent base configuration is installed from
  `packages/supervisor-agent/resources/{orin,pico}-modules.json`. Per-module
  additions belong in the configured `modules.d` directory and must be merged
  by the Agent; do not edit generated device files as a release change.
- Agent RPC credentials are provisioned by the packaged initializer and native
  Supervisor RPC adapters must use the configured credential file. Do not add
  a second credential scheme, ad-hoc service, or direct startup path.
- Deployment changes must preserve the documented sequence: target/robot
  identity validation, common and module package installation, configuration
  staging, Agent/Supervisor registration, then service start and release-state
  recording.
- New or changed release structure must update the relevant documentation and
  pass the architecture guard plus the release dry-run before merge.

## Required change procedure

1. Read `system_deployment/architecture/ARCHITECTURE.md` and `system_deployment/architecture/FILE_INDEX.md`.
2. Locate the unique owner of the behavior; do not copy a configuration or implementation into another directory.
3. Update references and the file index when moving or adding files.
4. Run `python3 system_deployment/architecture/check_architecture.py`.
5. Run `python3 -m py_compile` on changed Python files and `system_deployment/build/build_release.sh --dry-run`.
6. Record architecture-affecting changes with purpose, files, compatibility, validation and rollback/archive information.

## Forbidden changes

Do not restore `one_stop/`, create a second release configuration, put `.deb`/`.run`/`.firmware` artifacts in source directories, move device deployment into `build/`, or delete `common/`, `config/`, `assets/`, `packages/`, or `image/` files without an archive and reference scan.
