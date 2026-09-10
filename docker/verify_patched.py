"""Find, remove and then assert the absence of vulnerable package copies.

Two lessons are baked into this script.

First, a container image can hold several copies of the same distribution in
directories that are not on ``sys.path``: the Playwright base image carries the
distribution's own ``dist-packages``, and ``pip install --prefix`` on Debian
writes to a nested ``local/`` directory. A scanner reads every one of them, so
walking ``sys.path`` is not enough. This walks the filesystem.

Second, a purge that silently removes nothing looks identical to a purge that
worked. Every path found is printed, and the build fails if a copy below the
required version survives, so the build log says which directory it came from
instead of leaving that to a scan two jobs later.

    python3 verify_patched.py --purge     # remove old copies, then verify
    python3 verify_patched.py             # verify only
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

MINIMUM: dict[str, tuple[int, ...]] = {"setuptools": (78, 1, 1), "msgpack": (1, 2, 1)}
SKIP_DIRS = {"/proc", "/sys", "/dev", "/run", "/ms-playwright"}
METADATA_DIR = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)-(?P<version>[0-9][^-]*)\.(dist|egg)-info$")


def parse(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in version.split("."):
        digits = re.match(r"\d+", chunk)
        if not digits:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def walk(root: Path = Path("/")) -> list[tuple[str, str, Path]]:
    """Return (name, version, metadata directory) for every copy on disk."""
    found: list[tuple[str, str, Path]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if str(current) in SKIP_DIRS:
            continue
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            # Packages vendor their own dependencies; those are not the copy
            # an advisory is about and removing them breaks the parent.
            if "/_vendor/" in f"{entry}/":
                continue
            match = METADATA_DIR.match(entry.name)
            if match:
                name = match.group("name").replace("_", "-").lower()
                if name in MINIMUM:
                    found.append((name, match.group("version"), entry))
                continue
            stack.append(entry)
    return found


def purge(name: str, metadata_dir: Path) -> None:
    parent = metadata_dir.parent
    shutil.rmtree(metadata_dir, ignore_errors=True)
    for sibling in (name, name.replace("-", "_")):
        shutil.rmtree(parent / sibling, ignore_errors=True)
    if name == "setuptools":
        shutil.rmtree(parent / "pkg_resources", ignore_errors=True)


SBOM_PATTERNS = ("*.cdx.json", "*.spdx.json", "*.spdx", "*sbom*.json", "bom.json")


def stale_sboms(root: Path = Path("/")) -> list[Path]:
    """SBOM files baked into the base image.

    Scanners trust an SBOM they find inside an image over what is on disk
    (Trivy logs "Third-party SBOM may lead to inaccurate vulnerability
    detection" when it does). After the packages it describes have been
    upgraded, such a file is a stale claim and the source of findings that no
    amount of purging fixes.
    """
    found: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if str(current) in SKIP_DIRS:
            continue
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif any(entry.match(pattern) for pattern in SBOM_PATTERNS):
                found.append(entry)
    return found


def main(argv: list[str]) -> int:
    should_purge = "--purge" in argv
    removed: list[str] = []

    for sbom in stale_sboms():
        print(f"sbom {sbom}")
        if should_purge:
            sbom.unlink(missing_ok=True)
            removed.append(f"stale SBOM {sbom}")

    for name, version, metadata_dir in sorted(walk(), key=lambda item: str(item[2])):
        outdated = parse(version) < MINIMUM[name]
        print(f"{'OLD ' if outdated else 'ok  '} {name} {version}  {metadata_dir.parent}")
        if outdated and should_purge:
            purge(name, metadata_dir)
            removed.append(f"{name} {version} in {metadata_dir.parent}")

    for line in removed:
        print(f"removed {line}")

    survivors = [
        f"{name} {version} in {metadata_dir.parent}"
        for name, version, metadata_dir in walk()
        if parse(version) < MINIMUM[name]
    ]
    present = {name for name, _, _ in walk()}
    missing = [name for name in MINIMUM if name not in present]

    if survivors or missing:
        print("\nFAILED:", *survivors, *(f"{n} is missing entirely" for n in missing), sep="\n  ")
        return 1

    print("\nall copies are at or above the required versions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
