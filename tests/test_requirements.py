"""requirements.txt mirrors pyproject.toml, so pip users and uv users get the same ranges."""
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> list[str]:
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    return [line.split("#")[0].strip() for line in text.splitlines() if line.split("#")[0].strip()]


def test_every_pyproject_requirement_is_mirrored_exactly():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wanted = set(project["project"]["dependencies"]) | set(project["dependency-groups"]["dev"])
    missing = wanted - set(_requirements())
    assert not missing, f"pyproject.toml pins missing from requirements.txt: {sorted(missing)}"


def test_every_requirement_is_declared_in_pyproject():
    """The mirror runs both ways, or uv users silently lose a package.

    sqlite-vec and fastembed once lived in requirements.txt alone, so they never
    reached uv.lock and no uv user could import slice/retrieve.py.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(project["project"]["dependencies"]) | set(project["dependency-groups"]["dev"])
    missing = set(_requirements()) - declared
    assert not missing, f"requirements.txt pins missing from pyproject.toml (so absent from uv.lock): {sorted(missing)}"


def test_no_package_is_listed_twice():
    names = [re.match(r"[A-Za-z0-9_.\-]+", line).group(0).lower().replace("_", "-") for line in _requirements()]
    assert len(names) == len(set(names)), "one line per package, or pip may combine conflicting ranges"
