"""Health checks and first-run setup.

A slice of the ``levh`` CLI. The parsers and the dispatch chain stay in
server/cli.py; this module holds the implementations.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from server.commands.paths import DEFAULT_CONFIG, MCP_DIR
from server.core.runtime_config import CONFIG_DIR, CONFIG_FILE
from server.core.env import get_env
from server.core.runtime_config import resolve_runtime_config, runtime_env




def cmd_setup(args: argparse.Namespace) -> int:
    """First-run setup: initialize storage, choose demo/real mode, generate
    one focused MCP config, and write a privacy-safe local receipt."""
    import asyncio

    from server.configs import generate_config, normalize_platform
    from server.core import engine_provider
    from server.core.dogfood import dogfood_enabled
    from server.core.onboarding import write_receipt
    from server.tools.profiles import UnknownProfileError, resolve_profile

    if args.status:
        async def _status() -> dict:
            engine = engine_provider.get_engine()
            await engine.initialize()
            try:
                return await engine.onboarding_status()
            finally:
                await engine.shutdown()

        print(json.dumps(asyncio.run(_status()), indent=2, ensure_ascii=False))
        return 0

    mode = "demo" if args.demo else "real" if args.real else ""
    if not mode:
        if sys.stdin.isatty():
            choice = input("  Choose setup mode: [d]emo or [r]eal data? ").strip().lower()
            mode = "demo" if choice in {"d", "demo"} else "real" if choice in {"r", "real"} else ""
        if not mode:
            print("  Choose one: --demo or --real", file=sys.stderr)
            return 1

    try:
        platform = normalize_platform(args.client)
        profile = resolve_profile(args.profile)
    except (ValueError, UnknownProfileError) as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1

    # Initialize local config without ever overwriting an existing one.
    config_dir = Path(CONFIG_DIR)
    config_file = config_dir / CONFIG_FILE
    mcp_dir = config_dir / MCP_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    if not config_file.exists():
        cfg = dict(DEFAULT_CONFIG)
        if os.getenv("SQLITE_DB_PATH"):
            cfg["database_path"] = os.environ["SQLITE_DB_PATH"]
        if os.getenv("EMBEDDER_MODE"):
            cfg["embedder_mode"] = os.environ["EMBEDDER_MODE"]
        config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")

    # The config file may have been created in this process. Ensure a cached
    # test/embedded engine cannot retain the pre-setup path.
    engine_provider.set_engine(None)
    runtime = resolve_runtime_config()

    async def _run() -> tuple[dict, dict]:
        engine = engine_provider.get_engine()
        await engine.initialize()
        try:
            seed_result = {"seeded": 0, "skipped": False}
            if mode == "demo":
                seed_result = await engine.seed_demo(force=False)
            status = await engine.onboarding_status()
            return seed_result, status
        finally:
            await engine.shutdown()

    seed_result, status = asyncio.run(_run())

    generated = generate_config(
        platform,
        project_path=".",
        profile=profile,
        **runtime_env(runtime),
    )
    config_path = mcp_dir / f"{args.client}-{profile}.json"
    config_path.write_text(json.dumps(generated, indent=2) + "\n", encoding="utf-8")

    receipt = write_receipt(
        database_ready=True,
        first_memory_ready=bool(status.get("memory_count")),
        mcp_client=args.client,
        mcp_profile=profile,
        demo_mode=mode == "demo",
        dogfood_enabled=dogfood_enabled(),
    )

    print("\n  LEVH setup")
    print("  " + "=" * 50)
    print(f"  Mode:          {mode}")
    print(f"  Database:      ready ({status.get('memory_count', 0)} memories)")
    if mode == "demo":
        if seed_result.get("skipped"):
            print("  Demo data:     skipped (store already contains data)")
        else:
            print(f"  Demo data:     {seed_result.get('seeded', 0)} memories loaded")
    else:
        print("  Demo data:     not loaded")
    print(f"  MCP client:    {args.client}")
    print(f"  MCP profile:   {profile}")
    print(f"  MCP config:    {config_path}")
    print(f"  Dogfood:       {'ON' if receipt['dogfood_enabled'] else 'OFF (local default)'}")
    print("\n  MCP profiles narrow tool discovery; they are not an authorization boundary.")
    if mode == "real" and not status.get("memory_count"):
        print('  Next: levh capture "Atlas uses PostgreSQL in production."')
    print("  Next: levh serve")
    print("  Then test recall from your configured AI client.")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Create local config directory and default config file."""
    config_dir = Path(CONFIG_DIR)
    config_file = config_dir / CONFIG_FILE
    mcp_dir = config_dir / MCP_DIR

    if config_file.exists() and not args.force:
        print(f"  Config already exists: {config_file}")
        print(f"  Use --force to overwrite.")
        return 1

    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_dir.mkdir(parents=True, exist_ok=True)

    cfg = dict(DEFAULT_CONFIG)
    if args.embedder_mode:
        cfg["embedder_mode"] = args.embedder_mode
    if args.db_path:
        cfg["database_path"] = args.db_path

    config_file.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"  Created: {config_file}")
    print(f"  Created: {mcp_dir}/")
    print(f"  Config:  embedder_mode={cfg['embedder_mode']}, "
          f"database_path={cfg['database_path']}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Launch the FastAPI server via uvicorn."""
    import uvicorn
    runtime = resolve_runtime_config(
        explicit={"api_host": args.host, "api_port": args.port}
    )
    host = runtime.api_host
    port = runtime.api_port
    import ipaddress

    try:
        is_loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback and not get_env("LEVH_TOKEN", "").strip():
        print(
            "  Refusing non-loopback bind without LEVH_TOKEN. "
            "Set a strong token or bind to 127.0.0.1.",
            file=sys.stderr,
        )
        return 1
    print(f"  Starting LEVH API on {host}:{port}")
    print(f"  Dashboard: http://{host}:{port}/   API docs: http://{host}:{port}/docs")
    uvicorn.run("server.api:app", host=host, port=port, reload=args.reload)
    return 0
