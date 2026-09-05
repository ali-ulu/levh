"""Librarian — LEVH'in içinden hafızayı yöneten bekçi ajanı.

Sunucu açılınca arka plan görevi olarak başlar (api.lifespan). Periyodik:

  1. KEŞİF  — makinedeki ajan konfigürasyonlarını tarar (Cline, Claude Code,
             Codex); hangisi levh MCP'sine bağlı, hangisi değil?
  2. İZLEME — son 24 saatte hangi ajan hiç kayıt yapmamış? held_memories
             kuyruğu birikmiş mi?
  3. AKSİYON — bulguları ``source="librarian"`` hafıza olarak kaydeder.
             Yıkıcı işlem YAPMAZ — sadece öneri üretir.

Chat: ``POST /api/librarian/chat`` — soru + canlı bağlam LLM'e gider.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

logger = logging.getLogger("levh.librarian")

LIBRARIAN_SOURCE = "librarian"
DEFAULT_INTERVAL = 600  # 10 dk
_CHAT_HISTORY: list[dict] = []
_HISTORY_TURNS = 5  # LLM'e taşınan soru/yanıt çifti sayısı

# Tarama bir worker thread'inde koşar (``asyncio.to_thread``), ama motorun
# aiosqlite bağlantısı sunucunun event loop'una bağlıdır. Bulguyu yazmak için
# o loop'a geri dönmek gerekir; burada tutulan referans o köprüdür.
_OWNER_LOOP: asyncio.AbstractEventLoop | None = None

_SYSTEM_PROMPT = (
    "Sen LEVH'in kütüphane memurusun: hafıza katmanını içeriden yöneten "
    "ajansın. Görevin: makinedeki AI ajanlarını (Cline, Claude Code, Codex...) "
    "bulmak, levh araçlarını onlara bağlamak, hafıza kullanımını izlemek, "
    "bağlantı sorunlarını DÜZELTMEK, hafıza kalitesini korumak ve kullanıcıya "
    "Türkçe, kısa, net yanıt vermek. Sana verilen CONTEXT bloğu canlı "
    "veritabanı ve keşif verisidir; dışını tahmin etme.\n\n"
    "AKSİYON YETENEĞİN var: bir aksiyon gerekiyorsa yanıtının EN SONUNA şu "
    "biçimde bir JSON bloğu ekle:\n"
    "```json\n{\"action\": {\"type\": \"...\", ...}, \"reply\": \"kullanıcıya "
    "Türkçe açıklama\"}\n```\n"
    "Aksiyon tipleri:\n"
    "- {\"type\": \"shell\", \"command\": \"<terminal komutu>\"} — PowerShell "
    "komutu çalıştırır (kurulum, config düzenleme, teşhis). Max 120 sn.\n"
    "- {\"type\": \"add_levh_mcp\", \"agent\": \"cline\"|\"codex\"|\"claude-code\"} — "
    "o ajanın config'ine levh MCP sunucusunu ekler (yedek alır).\n"
    "- {\"type\": \"none\"} — aksiyon gerekmiyorsa.\n"
    "Kurallar: Her shell komutu loglanır. 'format', 'Remove-Item -Recurse "
    "C:\\', 'reg delete' gibi yıkıcı komutlar ENGELLENİR. Aynı aksiyonu "
    "tekrar tekrar önerme. reply alanını her zaman yaz."
)


def _db_path() -> str:
    """The database the ENGINE uses — not a guess at where it might live.

    Reading a different file than the engine writes to made the activity
    report describe an empty database and call every agent silent.
    """
    try:
        from server.core import engine_provider

        path = engine_provider.get_engine().db.db_path
        if path:
            return str(path)
    except Exception:  # noqa: BLE001 — motor yoksa (CLI) yapılandırmaya düş
        logger.debug("librarian could not read the engine db path")
    try:
        from server.core.runtime_config import resolve_runtime_config

        return resolve_runtime_config().database_path
    except Exception:  # noqa: BLE001 — config bozuksa rapor yine de çıksın
        return os.getenv(
            "SQLITE_DB_PATH",
            os.path.join(os.path.expanduser("~"), "AppData", "Local", "stackmemory.db"),
        )


def _ro_conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True, timeout=5)


# ── Keşif ─────────────────────────────────────────────────────────────


def _has_levh(text: str) -> bool:
    t = text.lower()
    return '"levh"' in t or "[mcp_servers.levh]" in t or "levh.exe" in t or "levh mcp" in t


def discover_agents() -> list[dict]:
    """Makinedeki tüm ajan konfigürasyonlarını tara; levh bağlantısı var mı?"""
    return [describe_agent(name) for name in _ALL_AGENTS]


# ── Aktivite izleme ───────────────────────────────────────────────────


def _activity_report() -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        conn = _ro_conn()
        try:
            per_source = dict(conn.execute(
                "SELECT source, COUNT(*) FROM memories WHERE created_at > ? GROUP BY source",
                (cutoff,),
            ).fetchall())
            held = conn.execute(
                "SELECT COUNT(*) FROM held_memories WHERE status='held'"
            ).fetchone()[0]
            last_mem = conn.execute("SELECT MAX(created_at) FROM memories").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("librarian activity query failed: %s", exc)
        return {"error": str(exc)}

    known = ["cline", "claude-code", "codex", "cli", "dashboard"]
    silent = [s for s in known if per_source.get(s, 0) == 0]
    return {
        "window_hours": 24,
        "memories_per_source": per_source,
        "silent_agents": silent,
        "held_memories": held,
        "last_memory_at": last_mem,
    }


def _llm_ready() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def set_owner_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Bulguların yazılacağı event loop'u kaydet (sunucu açılışında çağrılır)."""
    global _OWNER_LOOP
    _OWNER_LOOP = loop


def _target_loop() -> asyncio.AbstractEventLoop | None:
    """Motorun bağlı olduğu, hâlâ çalışan loop — yoksa None."""
    if _OWNER_LOOP is not None and _OWNER_LOOP.is_running():
        return _OWNER_LOOP
    try:  # sunucu ayakta ama start_background çağrılmadıysa (ör. sadece route)
        from server import api

        loop = api._event_loop
    except Exception:  # noqa: BLE001 — api import edilemiyorsa loop da yok
        return None
    return loop if loop is not None and loop.is_running() else None


async def _admit_finding(content: str) -> dict:
    from server.core import engine_provider

    engine = engine_provider.get_engine()
    await engine.initialize()  # idempotent; CLI/test yolunda bağlantıyı açar
    return await engine.admit_memory(
        content=content,
        importance=0.5,
        tags=["librarian", "rapor"],
        source=LIBRARIAN_SOURCE,
        memory_type="short_term",
    )


def _store_finding(content: str) -> None:
    """Bulguyu hafızaya yaz (admission gate'ten geçer).

    Üç çağrı bağlamı var ve üçü de çalışmak zorunda: (1) sunucunun loop'unun
    içinden (route), (2) ``to_thread`` worker'ından (periyodik tarama),
    (3) hiç loop olmayan bir süreçten (CLI, test). Yeni bir loop açıp motorun
    coroutine'ini orada koşturmak (2)'de sessizce patlıyordu — aiosqlite
    bağlantısı sunucunun loop'una bağlı.
    """
    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        if running is not None:
            running.create_task(_admit_finding(content))
            return

        target = _target_loop()
        if target is not None:
            future = asyncio.run_coroutine_threadsafe(_admit_finding(content), target)
            result = future.result(timeout=30)
        else:
            result = asyncio.run(_admit_finding(content))
        if not result.get("stored"):
            logger.debug(
                "librarian finding not stored (%s)",
                result.get("decision", {}).get("action"),
            )
    except Exception:  # noqa: BLE001 — rapor yazımı sunucuyu düşürmesin
        logger.exception("librarian could not store finding")


def scan(store_memory: bool = True) -> dict:
    """Tek tur keşif + izleme. Bulguları hafızaya da yazar."""
    report = {
        "at": datetime.now(timezone.utc).isoformat(),
        "agents": discover_agents(),
        "activity": _activity_report(),
    }

    if store_memory:
        unconnected = [a["agent"] for a in report["agents"] if not a["levh_connected"]]
        silent = report["activity"].get("silent_agents", [])
        held = report["activity"].get("held_memories", 0)
        lines = ["LEVH LIBRARIAN taraması:"]
        for a in report["agents"]:
            lines.append(f"- {a['agent']}: {'bagli' if a['levh_connected'] else 'levh MCP YOK'}")
        if unconnected:
            lines.append(f"- Bagli olmayan: {', '.join(unconnected)} (mcp.json'a levh eklenmeli)")
        if silent:
            lines.append(f"- Son 24s sessiz ajanlar: {', '.join(silent)}")
        if held:
            lines.append(f"- held_memories kuyrugunda {held} kayit (review bekliyor)")
        _store_finding("\n".join(lines))

    return report


# ── Aksiyon çalıştırıcı (terminal + config kurulumu) ──────────────────

# Tek ters bölü ile yazılır: kaçışlar iki kat yazıldığında desen `C:\` yerine
# `C:\\` arıyordu, yani prompt'ta "engellenir" denen komutun ta kendisi geçiyordu.
_BLOCKED_RE = re.compile(
    r"(?i)\bformat\b"
    r"|remove-item\b[^\n]*-recurse\b[^\n]*\b[a-z]:\\"  # sürücü kökünü özyineli sil
    r"|reg\s+delete"
    r"|rd\s+/s"
    r"|del\s+/[fq]\b[^\n]*\b[a-z]:\\"
    r"|diskpart"
    r"|cipher\s+/w"
    r"|bcdedit"
)


def _backup(path: Path) -> Path | None:
    try:
        bak = path.with_suffix(path.suffix + ".librarian-bak")
        shutil.copy2(path, bak)
        return bak
    except OSError:
        return None


# Makineye kurulu / yapılandırılmış tüm ajanlar. value: (CLI adı, [config yolları]).
_ALL_AGENTS = {
    "cline": ("cline", [Path.home() / ".cline" / "mcp.json"]),
    "claude-code": ("claude", [Path.home() / ".claude.json",
                               Path.home() / ".claude-code" / "mcp.json"]),
    "codex": ("codex", [Path.home() / ".codex" / "config.toml"]),
    "opencode": ("opencode", [Path.home() / ".opencode" / "mcp.json"]),
    "opencodex": ("opencodex", [Path.home() / ".opencodex" / "mcp.json"]),
    "jcode": ("jcode", [Path.home() / ".jcode" / "mcp.json"]),
    "kilo-code": ("kilocode", [Path.home() / ".kilocode" / "mcp.json",
                               Path.home() / ".kilo" / "kilo.json"]),
    "oh-my-cli": ("oh-my-cli", [Path.home() / ".oh-my-cli" / "mcp.json"]),
    "gemini": ("gemini", [Path.home() / ".gemini" / "config" / "mcp_config.json"]),
    # hermes ve aider'ın MCP config şeması bilinmiyor / henüz config'i yok.
    "hermes": ("hermes", []),
    "aider": ("aider", []),
}


def discover_installed() -> list[dict]:
    """PATH'te kurulu + yapılandırılmış tüm ajanları bul; levh bağlantısı var mı?"""
    out = []
    for name, (exe, configs) in _ALL_AGENTS.items():
        path = shutil.which(exe)
        connected = describe_agent(name)["levh_connected"]
        out.append({"agent": name, "installed": bool(path), "path": path,
                    "levh_connected": connected})
    return out


def describe_agent(agent: str) -> dict:
    """Bir ajanın config dosyalarında levh bağlantısı var mı?"""
    entry = _ALL_AGENTS.get(agent)
    if not entry:
        return {"agent": agent, "levh_connected": False, "configs": []}
    _, configs = entry
    configs_read = []
    for cfg in configs:
        if cfg.is_file():
            try:
                text = cfg.read_text(encoding="utf-8-sig", errors="ignore")
                configs_read.append({"config": str(cfg),
                                     "levh_connected": _has_levh(text)})
            except OSError:
                configs_read.append({"config": str(cfg), "levh_connected": False})
        else:
            configs_read.append({"config": str(cfg), "levh_connected": False})
    any_connected = any(c["levh_connected"] for c in configs_read)
    return {"agent": agent, "levh_connected": any_connected,
            "configs": configs_read}


def add_levh_mcp(agent: str) -> dict:
    """Ajanın config dosyasına levh MCP sunucusunu ekler (yedek alarak).

    Jenerik ``mcpServers`` JSON formatını kullanan ajanlar (opencode,
    opencodex, jcode, claude-code proje config'i) tek helper ile; cline ve
    codex kendi formatlarıyla ele alınır.
    """
    home = Path.home()
    levh_exe = shutil.which("levh") or r"C:\Users\sonfi\AppData\Local\Programs\Python\Python312\Scripts\levh.exe"
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
        data = json.loads(cfg.read_text(encoding="utf-8-sig", errors="ignore"))
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


def shell_enabled() -> bool:
    """Terminal yetkisi açık mı? (``LEVH_LIBRARIAN_SHELL=0`` ile kapatılır)"""
    return os.getenv("LEVH_LIBRARIAN_SHELL", "1").strip().lower() not in {
        "0", "false", "off",
    }


def run_shell(command: str) -> dict:
    """Librarian'ın terminal yetkisi — engelli desenler hariç.

    Desen listesi kara listedir, yani kanıt değil güvence: bilinen yıkıcı
    kalıpları durdurur, hepsini değil. Yetkiyi tümden kapatmak için
    ``LEVH_LIBRARIAN_SHELL=0``.
    """
    if not shell_enabled():
        return {"ok": False, "msg": "terminal yetkisi kapali (LEVH_LIBRARIAN_SHELL=0)"}
    if _BLOCKED_RE.search(command):
        _store_finding(f"LIBRARIAN GUVENLIK: yikici komut engellendi: {command[:120]}")
        return {"ok": False, "msg": "yikici komut engellendi"}
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        result = (out + ("\n[STDERR] " + err if err else ""))[:2000] or "(bos cikti)"
        _store_finding(f"LIBRARIAN SHELL: {command[:200]}\nSonuc: {result[:400]}")
        return {"ok": proc.returncode == 0, "msg": result}
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "komut 120 sn'de bitmedi"}
    except OSError as exc:
        return {"ok": False, "msg": f"shell hatasi: {exc}"}


def execute_action(action: dict) -> dict:
    """LLM'in önerdiği aksiyonu çalıştırır ve sonucu döner."""
    a_type = action.get("type", "none")
    if a_type == "shell":
        return run_shell(str(action.get("command", "")))
    if a_type == "add_levh_mcp":
        result = add_levh_mcp(str(action.get("agent", "")))
        _store_finding(f"LIBRARIAN MCP KURULUM: {action.get('agent')} -> {result}")
        return result
    if a_type == "none":
        return {"ok": True, "msg": "aksiyon gerekmedi"}
    return {"ok": False, "msg": f"bilinmeyen aksiyon tipi: {a_type}"}


_ACTION_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
# Bazen model sadece ham bir JSON nesnesi döner (fence yok). Hem bütün cevabın
# JSON'dan oluştuğu hem içinde JSON bloğu geçtiği durumları yakala.
_BARE_ACTION_RE = re.compile(r"^\s*\{.*\}\s*$", re.DOTALL)


def _parse_json_block(text: str) -> tuple[str, dict | None]:
    """Dönen metinden aksiyon JSON'unu ayıklar. Fenced ya da ham JSON.

    Sistem promptu modele açıklamayı JSON'un ``reply`` alanına yazmasını
    söylüyor; blok dışında metin kalmadığında kullanıcıya gösterilecek yanıt
    oradan alınır — yoksa modele tam uyan bir cevap boş baloncuk olarak
    görünüyordu.
    """
    match = _ACTION_RE.search(text)
    if not match:
        if _BARE_ACTION_RE.match(text):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict) and "action" in parsed:
                    return str(parsed.get("reply", "") or "").strip(), parsed.get("action")
            except json.JSONDecodeError:
                pass
        return text.strip(), None
    try:
        parsed = json.loads(match.group(1))
    except json.JSONDecodeError:
        return text.strip(), None
    reply = (text[: match.start()] + text[match.end():]).strip()
    if not reply:
        reply = str(parsed.get("reply", "") or "").strip()
    return reply, parsed.get("action")


def _split_reply_and_action(text: str) -> tuple[str, dict | None]:
    return _parse_json_block(text)


async def run_loop(interval: int = DEFAULT_INTERVAL) -> None:
    logger.info("Librarian loop started (interval=%ss)", interval)
    while True:
        try:
            await asyncio.to_thread(scan, True)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("librarian scan failed")
        await asyncio.sleep(interval)


def start_background() -> asyncio.Task:
    interval = int(os.getenv("LEVH_LIBRARIAN_INTERVAL", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)
    loop = asyncio.get_running_loop()
    set_owner_loop(loop)
    return loop.create_task(run_loop(interval))


# ── Chat ──────────────────────────────────────────────────────────────


def _context_block() -> str:
    report = scan(store_memory=False)
    report["installed_agents"] = discover_installed()
    return "CONTEXT:\n" + json.dumps(report, ensure_ascii=False, indent=1)


async def chat(question: str) -> dict:
    """Kullanıcı sorusu + canlı bağlam → LLM → (önerilen aksiyonu çalıştır) → yanıt."""
    context = _context_block()
    # Geçmiş olmadan widget her soruyu ilk soru sanıyordu ("evet, yap" gibi bir
    # yanıt havada kalıyordu). Son turlar sistem promptundan sonra taşınır.
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        *_CHAT_HISTORY[-_HISTORY_TURNS * 2:],
        {"role": "user", "content": f"{context}\n\nSORU: {question}"},
    ]

    if not _llm_ready():
        return {"answer": "LLM beyin ayarli degil (OPENAI_API_KEY yok).\n" + context,
                "backend": "offline", "actions": []}

    headers = {
        "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}",
        "Content-Type": "application/json",
    }
    url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")
    model = os.getenv("SUMMARY_MODEL", "nvidia/nemotron-3.5-lightning:free")

    executed: list[dict] = []
    reply = ""
    for _step in range(3):  # max 3 tur: düşün → aksiyon → sonuç → yanıt
        payload = {"model": model, "messages": messages, "temperature": 0.3}
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                reply = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001 — chat asla 500 dömesin
            logger.warning("librarian chat LLM failed: %s", exc)
            reply = f"LLM'e su an ulasamadim ({exc})."
            break

        reply_text, action = _split_reply_and_action(reply)
        if not action or action.get("type") == "none":
            reply = reply_text
            break

        result = await asyncio.to_thread(execute_action, action)
        executed.append({"action": action, "result": result})
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": (
            f"AKSIYON SONUCU: {json.dumps(result, ensure_ascii=False)}\n"
            "Bu sonucu kullaniciya Turkce ozetle; baska aksiyon gerekiyorsa "
            "yeni JSON bloğunu ekle, gerekmiyorsa 'none' yaz."
        )})
        reply = reply_text

    if not reply.strip():
        # Sohbet penceresine boş baloncuk düşürmektense ne olduğunu yaz.
        if executed:
            reply = "Aksiyon calistirildi: " + json.dumps(
                executed[-1]["result"], ensure_ascii=False
            )
        else:
            reply = "Model bos yanit dondu; soruyu yeniden sorar misin?"

    _CHAT_HISTORY.append({"role": "user", "content": question})
    _CHAT_HISTORY.append({"role": "assistant", "content": reply})
    del _CHAT_HISTORY[:-20]
    return {"answer": reply, "backend": "llm", "actions": executed}