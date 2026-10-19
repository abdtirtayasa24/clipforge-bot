"""Fails when a direct dependency in pyproject.toml lacks an exact `==` pin (ADR-001)."""

import re
import sys
import tomllib
from pathlib import Path

_EXACT_PIN = re.compile(r"^[A-Za-z0-9._-]+(\[[A-Za-z0-9._,-]+\])?==[A-Za-z0-9.+!-]+$")


def unpinned_requirements(pyproject: Path) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    requirements = [
        *data.get("build-system", {}).get("requires", []),
        *project.get("dependencies", []),
        *(req for group in project.get("optional-dependencies", {}).values() for req in group),
    ]
    return [req for req in requirements if not _EXACT_PIN.match(req.replace(" ", ""))]


def main() -> int:
    pyproject = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pyproject.toml")
    unpinned = unpinned_requirements(pyproject)
    for requirement in unpinned:
        sys.stderr.write(f"not exactly pinned: {requirement}\n")
    return 1 if unpinned else 0


if __name__ == "__main__":
    sys.exit(main())
