from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def levh_version() -> str:
    try:
        return version("levh")
    except PackageNotFoundError:
        path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        return str(tomllib.loads(path.read_text(encoding="utf-8"))["project"]["version"])


def main() -> int:
    legacy = Path(sys.argv[0]).stem.lower() == "stackmemory"
    if "--version" in sys.argv[1:]:
        if legacy:
            print("'stackmemory' is deprecated; use 'levh'", file=sys.stderr)
        print(f"{'stackmemory' if legacy else 'levh'} {levh_version()}")
        return 0
    from server.cli import main as cli_main
    return cli_main()
