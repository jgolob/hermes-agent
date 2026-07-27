#!/usr/bin/env python3
"""Enforce the shared Hermes-home contract during privileged bootstrap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Direct script execution puts scripts/ rather than the repository root on
# sys.path. Docker invokes this from the immutable source tree.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hermes_constants import enforce_shared_hermes_home  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--gid", type=int)
    args = parser.parse_args()

    errors = enforce_shared_hermes_home(args.home, gid=args.gid)
    if errors:
        print(
            f"[shared-home] WARNING: could not enforce permissions on "
            f"{len(errors)} operation(s):",
            file=sys.stderr,
        )
        for error in errors:
            print(f"[shared-home]   {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
