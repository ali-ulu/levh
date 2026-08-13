from pathlib import Path

from server import entrypoint
from scripts import release


def test_cli_version_comes_from_package_metadata():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'levh = "server.entrypoint:main"' in pyproject
    assert 'stackmemory = "server.entrypoint:main"' in pyproject
    assert entrypoint.levh_version() == release._pyproject_version()
