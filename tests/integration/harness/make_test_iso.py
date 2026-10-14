#!/usr/bin/env python3
"""Build the dev test ISO: stock LMDE ISO + overlay squashfs containing
the working tree's installer (and the live-installer-auto systemd unit).

Usage:
    make_test_iso.py [--iso fixtures/lmde-7-cinnamon-64bit.iso]
                     [--output fixtures/lmde-7-dev.iso]

Rebuild whenever installer code changes; the overlay build takes a few
seconds (only our files are compressed, the main squashfs is untouched).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import isotools  # noqa: E402

HERE = Path(__file__).resolve().parent
INTEGRATION_DIR = HERE.parent
REPO_ROOT = INTEGRATION_DIR.parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iso",
        default=str(INTEGRATION_DIR / "fixtures" / "lmde-7-cinnamon-64bit.iso"),
    )
    parser.add_argument(
        "--output",
        default=str(INTEGRATION_DIR / "fixtures" / "lmde-7-dev.iso"),
    )
    parser.add_argument("--source-tree", default=str(REPO_ROOT))
    args = parser.parse_args()

    if not Path(args.iso).exists():
        sys.exit(f"source ISO not found: {args.iso}")

    try:
        output = isotools.build_dev_iso(
            args.iso, args.source_tree, args.output,
            INTEGRATION_DIR / ".work" / "iso-build",
        )
    except isotools.IsoToolsError as exc:
        sys.exit(f"ERROR: {exc}")
    print(f"dev ISO written: {output}")


if __name__ == "__main__":
    main()
