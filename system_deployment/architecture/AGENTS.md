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

## Required change procedure

1. Read `system_deployment/architecture/ARCHITECTURE.md` and `system_deployment/architecture/FILE_INDEX.md`.
2. Locate the unique owner of the behavior; do not copy a configuration or implementation into another directory.
3. Update references and the file index when moving or adding files.
4. Run `python3 system_deployment/architecture/check_architecture.py`.
5. Run `python3 -m py_compile` on changed Python files and `system_deployment/build/build_release.sh --dry-run`.
6. Record architecture-affecting changes with purpose, files, compatibility, validation and rollback/archive information.

## Forbidden changes

Do not restore `one_stop/`, create a second release configuration, put `.deb`/`.run`/`.firmware` artifacts in source directories, move device deployment into `build/`, or delete `common/`, `config/`, `assets/`, `packages/`, or `image/` files without an archive and reference scan.
