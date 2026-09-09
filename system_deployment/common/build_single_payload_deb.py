#!/usr/bin/env python3
"""Compatibility entry point for the one-payload common carrier builder.

``orin-common`` used to rebuild the upstream libyaml package and merely rename
the output file. It now uses the same carrier implementation as every other
target, so the Debian package has its own Navi identity and can carry profile,
configuration and integrity metadata safely.
"""

from build_offline_common_bundle import main


if __name__ == "__main__":
    raise SystemExit(main())
