"""Build-time assertion that no vulnerable copy of a patched package survived.

Run inside the image build. It walks every directory on the interpreter path,
not just the active distribution metadata, because the failure this catches is
exactly a second copy sitting in a directory nobody was looking at.
"""

from __future__ import annotations

import re
import site
import sys
from pathlib import Path

MINIMUM = {"setuptools": (78, 1, 1), "msgpack": (1, 2, 1)}
DIST_INFO = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)-(?P<version>[0-9][^-]*)\.(dist|egg)-info$")


def parse(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.split("."):
        digits = re.match(r"\d+", chunk)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def main() -> int:
    directories = {Path(p) for p in [*site.getsitepackages(), *sys.path] if p and Path(p).is_dir()}
    found: dict[str, list[str]] = {name: [] for name in MINIMUM}
    failures: list[str] = []

    for directory in sorted(directories):
        for entry in directory.iterdir():
            match = DIST_INFO.match(entry.name)
            if not match:
                continue
            name = match.group("name").replace("_", "-").lower()
            if name not in MINIMUM:
                continue
            version = match.group("version")
            found[name].append(f"{version} in {directory}")
            if parse(version) < MINIMUM[name]:
                failures.append(f"{name} {version} found in {directory}")

    for name, sightings in found.items():
        print(f"{name}: {', '.join(sightings) or 'not installed'}")
        if not sightings:
            failures.append(f"{name} is missing entirely; the purge removed too much")

    if failures:
        print("\nFAILED:", *failures, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
