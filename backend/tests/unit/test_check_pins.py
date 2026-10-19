from pathlib import Path

from scripts.check_pins import unpinned_requirements

PYPROJECT = """
[build-system]
requires = ["hatchling{build}"]

[project]
dependencies = ["fastapi==0.1.0", "psycopg[binary]{runtime}"]

[project.optional-dependencies]
dev = ["pytest==9.0.0", "ruff{dev}"]
"""


def write(tmp_path: Path, build: str = "==1", runtime: str = "==3", dev: str = "==0.1") -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT.format(build=build, runtime=runtime, dev=dev), encoding="utf-8")
    return path


def test_fully_pinned_project_passes(tmp_path: Path) -> None:
    assert unpinned_requirements(write(tmp_path)) == []


def test_reports_every_unpinned_requirement(tmp_path: Path) -> None:
    path = write(tmp_path, build=">=1", runtime="", dev="~=0.1")

    assert unpinned_requirements(path) == ["hatchling>=1", "psycopg[binary]", "ruff~=0.1"]


def test_rejects_wildcard_and_compound_pins(tmp_path: Path) -> None:
    path = write(tmp_path, runtime="==3.*", dev="==0.1,<1")

    assert unpinned_requirements(path) == ["psycopg[binary]==3.*", "ruff==0.1,<1"]


def test_backend_pyproject_is_fully_pinned() -> None:
    backend = Path(__file__).resolve().parents[2]

    assert unpinned_requirements(backend / "pyproject.toml") == []
