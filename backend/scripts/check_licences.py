"""Fails the build if an installed dependency has a licence the closed-source decision excludes.

ADR 0008 records the decision. Test-only tools are excluded because they do not ship.
"""

from __future__ import annotations

import json
import subprocess
import sys

BLOCKED_MARKERS = ("AGPL", "AFFERO", "SSPL", "GNU GENERAL PUBLIC", "GPL")
ALLOWED_MARKERS = ("LGPL", "LESSER GENERAL PUBLIC", "GPL WITH", "GPLV2 WITH")
DEV_ONLY = {"pip-audit", "pip-licenses", "pgserver"}


def is_blocked(licence: str) -> bool:
    upper = licence.upper()
    if any(marker in upper for marker in ALLOWED_MARKERS):
        return False
    return any(marker in upper for marker in BLOCKED_MARKERS)


def main() -> int:
    output = subprocess.run(
        [sys.executable, "-m", "piplicenses", "--format=json", "--with-system"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    packages = json.loads(output)
    blocked = [
        package
        for package in packages
        if package["Name"].lower() not in DEV_ONLY and is_blocked(package["License"])
    ]
    for package in blocked:
        print(f"BLOCKED {package['Name']} {package['Version']}: {package['License']}")
    print(f"{len(packages)} packages checked, {len(blocked)} blocked")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
