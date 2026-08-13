# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small command-line entry point; the memory API remains the primary surface."""
from __future__ import annotations

import argparse
import json
import platform

from . import __version__


def main() -> int:
    parser = argparse.ArgumentParser(prog="horizon", description="Horizon Memory utilities")
    parser.add_argument("--version", action="store_true", help="print the installed version")
    parser.add_argument("--doctor", action="store_true", help="print a local runtime diagnostic")
    args = parser.parse_args()
    if args.version:
        print(__version__)
        return 0
    if args.doctor:
        print(json.dumps({
            "horizon_memory": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "core_network_required": False,
            "core_model_required": False,
        }, sort_keys=True))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
