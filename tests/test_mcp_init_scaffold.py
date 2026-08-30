"""`levh mcp init` — scaffolding a new MCP server.

A scaffold that writes files nobody can run is worse than no scaffold, so the
central test here imports the generated module and calls one of its tools.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from server.scaffold import ScaffoldError, generate_project, module_name, normalize_name

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Naming ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["srv", "my-server", "my_server", "Server.v2"])
def test_reasonable_names_are_accepted(name):
    assert normalize_name(name) == name


@pytest.mark.parametrize("name", ["", "   ", "9lives", "-dash", "has space", "../escape", "a/b"])
def test_unusable_names_are_refused(name):
    with pytest.raises(ScaffoldError):
        normalize_name(name)


def test_a_project_name_becomes_an_importable_module():
    assert module_name("my-server") == "my_server"
    assert module_name("Server.v2") == "server_v2"


# ── What gets written ────────────────────────────────────────────────


def test_a_project_has_everything_needed_to_run(tmp_path):
    generate_project("my-server", tmp_path, with_memory=True)

    root = tmp_path / "my-server"
    for expected in [
        "my_server/__init__.py",
        "my_server/server.py",
        "README.md",
        ".env.example",
        "requirements.txt",
        ".gitignore",
    ]:
        assert (root / expected).is_file(), f"missing {expected}"


def test_memory_projects_depend_on_levh_and_plain_ones_do_not(tmp_path):
    generate_project("with-mem", tmp_path, with_memory=True)
    generate_project("without-mem", tmp_path, with_memory=False)

    assert "levh" in (tmp_path / "with-mem" / "requirements.txt").read_text(encoding="utf-8")
    assert "levh" not in (tmp_path / "without-mem" / "requirements.txt").read_text(encoding="utf-8")


def test_the_env_example_never_ships_a_real_secret(tmp_path):
    generate_project("srv", tmp_path, with_memory=True)

    env = (tmp_path / "srv" / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY" in env
    # Present as a commented placeholder, never as a live value.
    assert "# OPENAI_API_KEY=sk-..." in env
    assert not any(
        line.startswith("OPENAI_API_KEY=") and len(line) > len("OPENAI_API_KEY=")
        for line in env.splitlines()
    )


def test_the_generated_gitignore_covers_the_database_and_env(tmp_path):
    generate_project("srv", tmp_path, with_memory=True)

    ignored = (tmp_path / "srv" / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignored
    assert "*.db" in ignored


def test_the_chosen_profile_reaches_the_generated_server(tmp_path):
    generate_project("srv", tmp_path, with_memory=True, profile="minimal")

    source = tmp_path / "srv" / "srv" / "server.py"
    assert 'MEMORY_PROFILE = "minimal"' in source.read_text(encoding="utf-8")


def test_an_unknown_template_is_refused(tmp_path):
    with pytest.raises(ScaffoldError, match="unknown template"):
        generate_project("srv", tmp_path, template="rust")


def test_an_existing_project_is_not_overwritten_by_accident(tmp_path):
    generate_project("srv", tmp_path)

    with pytest.raises(ScaffoldError, match="already exists"):
        generate_project("srv", tmp_path)

    # ...but --force is allowed to write into it.
    generate_project("srv", tmp_path, force=True)


# ── The part that matters: does it run? ──────────────────────────────


def _run_generated(root: Path, module: str, snippet: str, db_path: Path) -> str:
    env = {
        **os.environ,
        "EMBEDDER_MODE": "hash",
        "SQLITE_DB_PATH": str(db_path),
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(root)]),
    }
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet).replace("MODULE", module)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result.stdout


def test_a_generated_memory_server_mounts_tools_and_stores_a_memory(tmp_path):
    """The generated server must actually work, not merely import.

    In particular the engine has to be opened by the server's own event loop —
    an engine initialized in a separate asyncio.run() is bound to a loop that
    is already closed, and every tool call then hangs.
    """
    generate_project("srv", tmp_path, with_memory=True)
    root = tmp_path / "srv"

    out = _run_generated(
        root,
        "srv",
        """
        import asyncio
        import MODULE.server as srv

        async def main():
            tools = await srv.mcp.list_tools()
            names = {t.name for t in tools}
            assert "store_memory" in names, "memory tools were not mounted"
            assert "hello" in names, "the project's own tool was lost"

            async with srv._lifespan(srv.mcp):
                await srv.mcp.call_tool(
                    "store_memory", {"content": "written by the generated server"}
                )
            print("OK")

        asyncio.run(main())
        """,
        tmp_path / "generated.db",
    )
    assert "OK" in out


def test_a_generated_plain_server_runs_without_levh(tmp_path):
    generate_project("plain", tmp_path, with_memory=False)
    root = tmp_path / "plain"

    out = _run_generated(
        root,
        "plain",
        """
        import asyncio
        import MODULE.server as srv

        async def main():
            names = {t.name for t in await srv.mcp.list_tools()}
            assert names == {"hello"}, names
            print("OK")

        asyncio.run(main())
        """,
        tmp_path / "unused.db",
    )
    assert "OK" in out


# ── CLI wiring ───────────────────────────────────────────────────────


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "server.cli", *args],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "EMBEDDER_MODE": "hash"},
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_the_cli_creates_a_project(tmp_path):
    result = _cli("mcp", "init", "cli-server", "--with-memory", "--directory", str(tmp_path),
                  cwd=REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cli-server" / "cli_server" / "server.py").is_file()
    assert "Next:" in result.stdout


def test_the_cli_reports_a_bad_name_instead_of_creating_junk(tmp_path):
    result = _cli("mcp", "init", "9lives", "--directory", str(tmp_path), cwd=REPO_ROOT)

    assert result.returncode == 1
    assert "invalid project name" in result.stderr
    assert not list(tmp_path.iterdir())


def test_the_cli_reports_an_unknown_profile(tmp_path):
    result = _cli("mcp", "init", "srv", "--profile", "spicy", "--directory", str(tmp_path),
                  cwd=REPO_ROOT)

    assert result.returncode == 1
    assert "unknown MCP profile" in result.stderr


def test_adding_init_did_not_displace_its_neighbours():
    """server/cli.py keeps parsers and dispatch far apart; a new subcommand has
    historically overwritten the one next to it."""
    result = _cli("mcp", "--help", cwd=REPO_ROOT)

    assert result.returncode == 0
    for command in ("config", "profiles", "stdio", "init"):
        assert command in result.stdout, f"'{command}' vanished from `levh mcp --help`"


# ── Deploy targets ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("fly", "fly.toml"),
        ("railway", "railway.json"),
        ("render", "render.yaml"),
        ("docker", None),
    ],
)
def test_each_target_writes_its_config_plus_a_dockerfile(tmp_path, target, expected):
    generate_project("srv", tmp_path, with_memory=True, deploy=target)

    root = tmp_path / "srv"
    assert (root / "Dockerfile").is_file(), "every target builds the same image"
    if expected:
        assert (root / expected).is_file()


def test_nothing_deploy_related_is_written_by_default(tmp_path):
    generate_project("srv", tmp_path, with_memory=True)

    root = tmp_path / "srv"
    assert not (root / "Dockerfile").exists()
    assert not (root / "fly.toml").exists()


def test_an_unknown_deploy_target_is_refused(tmp_path):
    with pytest.raises(ScaffoldError, match="unknown deploy target"):
        generate_project("srv", tmp_path, deploy="heroku")


def test_the_generated_fly_config_parses_as_toml(tmp_path):
    import tomllib

    generate_project("srv", tmp_path, with_memory=True, deploy="fly")
    config = tomllib.loads((tmp_path / "srv" / "fly.toml").read_text(encoding="utf-8"))

    assert config["app"] == "srv"
    # The mount is the point: without it the database is ephemeral.
    assert config["mounts"][0]["destination"] == "/data"
    assert config["env"]["SQLITE_DB_PATH"].startswith("/data")


def test_the_generated_railway_config_parses_as_json(tmp_path):
    import json as _json

    generate_project("srv", tmp_path, with_memory=True, deploy="railway")
    config = _json.loads((tmp_path / "srv" / "railway.json").read_text(encoding="utf-8"))

    assert config["build"]["builder"] == "DOCKERFILE"
    assert "srv.server" in config["deploy"]["startCommand"]


def test_the_generated_render_config_parses_as_yaml(tmp_path):
    yaml = pytest.importorskip("yaml")

    generate_project("srv", tmp_path, with_memory=True, deploy="render")
    config = yaml.safe_load((tmp_path / "srv" / "render.yaml").read_text(encoding="utf-8"))

    service = config["services"][0]
    assert service["dockerfilePath"] == "./Dockerfile"
    assert service["disk"]["mountPath"] == "/data"


def test_the_image_stores_the_database_on_the_volume(tmp_path):
    """An ephemeral filesystem would lose the memory on every restart."""
    generate_project("srv", tmp_path, with_memory=True, deploy="docker")

    dockerfile = (tmp_path / "srv" / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV SQLITE_DB_PATH=/data/levh.db" in dockerfile
    assert 'VOLUME ["/data"]' in dockerfile
    assert 'CMD ["python", "-m", "srv.server"]' in dockerfile


def test_the_cli_passes_the_deploy_flag_through(tmp_path):
    result = _cli("mcp", "init", "dep-server", "--with-memory", "--deploy", "fly",
                  "--directory", str(tmp_path), cwd=REPO_ROOT)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dep-server" / "fly.toml").is_file()


def test_the_cli_reports_an_unknown_deploy_target(tmp_path):
    result = _cli("mcp", "init", "srv", "--deploy", "heroku",
                  "--directory", str(tmp_path), cwd=REPO_ROOT)

    assert result.returncode == 1
    assert "unknown deploy target" in result.stderr
