"""Agent config setup — the librarian's single remaining write power.

Split out of the single-file librarian (issue #98). This module owns
``add_levh_mcp``: it adds the levh MCP registration to KNOWN agent config
files, backing each file up first. This is a boundary, not a "for now":
adding a new action type means writing down what it may not do, too.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
import itertools


# datetime.now can repeat across rapid calls on some platforms (coarse clock
# granularity on Windows), which would silently overwrite the first backup.
# time.time_ns has the same hazard, so ties are broken with a process-local
# counter: stamps remain unique within a process no matter how fast calls come.
_stamp_tiebreak = itertools.count()


def _timestamp_stamp() -> str:
    wall = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{wall}-{next(_stamp_tiebreak)}"


def _backup(path: Path) -> Path | None:
    """Config'i değiştirmeden önce zaman damgalı bir kopya bırak.

    Damga şart: sabit adlı tek bir ``.bak`` her çalıştırmada kendini ezerdi,
    yani ikinci bir hatalı yazımdan sonra geri dönülecek sağlam kopya kalmazdı
    — yedek almanın tek sebebi buyken.
    """
    try:
        stamp = _timestamp_stamp()
        bak = path.with_suffix(f"{path.suffix}.{stamp}.librarian-bak")
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


def _levh_executable_fallback() -> str:
    """``levh`` PATH'te bulunamazsa makul bir yol üret.

    Sunucu bir sanal ortamdan koşuyorsa konsol betiği o ortamın Scripts/bin
    dizinindedir ve PATH'te olmayabilir; oradan bulmak, yazdığımız config'in
    gerçekten çalışması demek.
    """
    scripts = Path(sys.executable).parent
    for candidate in (scripts / "levh.exe", scripts / "levh",
                      scripts / "Scripts" / "levh.exe"):
        if candidate.is_file():
            return str(candidate)
    return "levh"


def add_levh_mcp(agent: str) -> dict:
    """Ajanın config dosyasına levh MCP sunucusunu ekler (yedek alarak).

    Jenerik ``mcpServers`` JSON formatını kullanan ajanlar (opencode,
    opencodex, jcode, claude-code proje config'i) tek helper ile; cline ve
    codex kendi formatlarıyla ele alınır.
    """
    home = Path.home()
    # PATH'te yoksa kendi yorumlayıcımızın Scripts/bin dizinine bak; oraya da
    # düşmezse "levh" adının kendisi kalır. Buraya bir makinenin mutlak yolunu
    # gömmek, o config'i başka her makinede bozuk üretir.
    levh_exe = shutil.which("levh") or _levh_executable_fallback()
    env_block = {
        "SQLITE_DB_PATH": os.getenv("SQLITE_DB_PATH", str(home / "AppData/Local/stackmemory.db")),
        "EMBEDDER_MODE": "auto",
        "SHORT_TERM_MAX": "50",
        "LEVH_MCP_PROFILE": "work",
    }

    def _add_json_mcp(json_path: Path) -> dict:
        """mcpServers şemasındaki bir JSON config'e levh bloğunu ekle."""
        if not json_path.is_file():
            json_path.parent.mkdir(parents=True, exist_ok=True)
            data = {"mcpServers": {}}
        else:
            _backup(json_path)
            try:
                data = json.loads(json_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                return {"ok": False, "msg": f"{json_path} gecersiz JSON"}
            data.setdefault("mcpServers", {})
        if "levh" in data["mcpServers"]:
            return {"ok": True, "msg": f"{json_path} zaten levh'e bagli"}
        data["mcpServers"]["levh"] = {
            "command": levh_exe, "args": ["mcp", "stdio"],
            "cwd": str(home), "env": env_block,
        }
        json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "msg": f"levh eklendi: {json_path}"}

    # Jenerik mcpServers JSON ajanları
    if agent in {"opencode", "opencodex", "jcode", "kilo-code", "oh-my-cli", "gemini"}:
        if agent == "gemini":
            cfg = home / ".gemini" / "config" / "mcp_config.json"
        elif agent == "kilo-code":
            cfg = home / ".kilocode" / "mcp.json"
        else:
            cfg = home / f".{agent}" / "mcp.json"
        return _add_json_mcp(cfg)

    if agent == "claude-code":
        proj_cfg = home / ".claude-code" / "mcp.json"
        if proj_cfg.is_file():
            res = _add_json_mcp(proj_cfg)
            if res.get("ok"):
                return res
        # global .claude.json
        cfg = home / ".claude.json"
        if not cfg.is_file():
            return {"ok": False, "msg": ".claude.json bulunamadi"}
        _backup(cfg)
        # errors="ignore" YOK: .claude.json oturum durumunu taşır ve bu kod
        # dosyayı ayrıştırıp baştan yazıyor. Çözülemeyen bir baytı sessizce
        # atmak, geri yazarken o baytın kalıcı kaybı demek — okunamıyorsa
        # dosyaya hiç dokunmamak doğru davranış.
        try:
            data = json.loads(cfg.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"ok": False, "msg": f".claude.json okunamadi, dokunulmadi: {exc}"}
        mcp = data.setdefault("mcpServers", {})
        if "levh" in mcp:
            return {"ok": True, "msg": "claude-code zaten levh'e bagli"}
        mcp["levh"] = {"command": levh_exe, "args": ["mcp", "stdio"],
                       "cwd": str(home), "env": env_block}
        cfg.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "msg": f"claude-code config'e levh eklendi: {cfg}"}

    if agent == "cline":
        cfg = home / ".cline" / "mcp.json"
        if not cfg.is_file():
            cfg.parent.mkdir(parents=True, exist_ok=True)
            data: dict = {"mcpServers": {}}
        else:
            _backup(cfg)
            data = json.loads(cfg.read_text(encoding="utf-8-sig"))
            data.setdefault("mcpServers", {})
        if "levh" in data["mcpServers"]:
            return {"ok": True, "msg": "cline zaten levh'e bagli"}
        data["mcpServers"]["levh"] = {
            "command": levh_exe, "args": ["mcp", "stdio"],
            "cwd": str(home), "env": env_block,
        }
        cfg.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {"ok": True, "msg": f"cline config'e levh eklendi: {cfg}"}

    if agent == "codex":
        cfg = home / ".codex" / "config.toml"
        if not cfg.is_file():
            return {"ok": False, "msg": "codex config.toml bulunamadi"}
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        if "[mcp_servers.levh]" in text:
            return {"ok": True, "msg": "codex zaten levh'e bagli"}
        _backup(cfg)
        block = (
            f'\n[mcp_servers.levh]\ncommand = "{levh_exe}"\nargs = ["mcp", "stdio"]\n'
            + "[mcp_servers.levh.env]\n"
            + "\n".join(f'{k} = "{v}"' for k, v in env_block.items())
            + "\n"
        )
        cfg.write_text(text.rstrip() + "\n" + block, encoding="utf-8")
        return {"ok": True, "msg": f"codex config'e levh eklendi: {cfg}"}

    return {"ok": False, "msg": f"bilinmeyen ajan: {agent}"}
