"""Librarian — LEVH'in içinden hafızayı izleyen bekçi ajanı.

Sunucu açılınca arka plan görevi olarak başlar (api.lifespan). Periyodik:

  1. KEŞİF  — makinedeki ajan konfigürasyonlarını tarar (Cline, Claude Code,
             Codex); hangisi levh MCP'sine bağlı, hangisi değil?
  2. İZLEME — hafıza aktivitesi ve held_memories kuyruğu.
  3. RAPOR  — bulguları ``findings`` gelen kutusuna yazar. Karar insanındır.

Chat: ``POST /api/librarian/chat`` — soru + canlı bağlam LLM'e gider.

YETKİ SINIRI — burada bilerek yapılmayan şey:

Bu modül keyfi terminal komutu ÇALIŞTIRMAZ. Daha önce çalıştırıyordu: model
bir ``shell`` aksiyonu önerirse PowerShell'e gidiyordu ve tek koruma bir kara
liste regex'iydi. O regex kaçış hatası yüzünden hiçbir şeyi tutmuyordu —
``Remove-Item -Recurse -Force <HOME>`` bile geçiyordu — ama regex düzeltilse
bile tasarım yanlıştı: kimlik doğrulaması varsayılan olarak kapalı olan bir
uçtan, modelin ürettiği metne bakarak komut çalıştırmak, sunucuya istek
atabilen herkese makinede kod çalıştırma yetkisi vermek demektir. Kara liste
bunu daraltmaz, sadece daraltıyormuş gibi gösterir.

Kalan tek yazma yetkisi ``add_levh_mcp``: kapsamı bilinen ajan config
dosyalarına levh MCP kaydını ekler, önce yedek alır, dosyayı ayrıştırıp geri
yazar. Bu bir sınırdır, "şimdilik böyle" değil — yeni bir aksiyon tipi
eklemek, o tipin neyi yapamayacağını da yazmayı gerektirir.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from server.core import llm_endpoint
from server.core.findings import build_row as build_finding_row

logger = logging.getLogger("levh.librarian")

LIBRARIAN_SOURCE = "librarian"
DEFAULT_INTERVAL = 600  # 10 dk
# Kuyruktaki birkaç kayıt normal çalışmadır; bulgu olması için birikmesi gerek.
HELD_QUEUE_THRESHOLD = 20
# Prompt'a taşınan önceki mesaj sayısı (soru + cevap = 2 mesaj).
CHAT_HISTORY_TURNS = 20
_CHAT_HISTORY: list[dict] = []

_SYSTEM_PROMPT = (
    "Sen LEVH'in kütüphane memurusun: hafıza katmanını içeriden yöneten "
    "ajansın. Görevin: makinedeki AI ajanlarını (Cline, Claude Code, Codex...) "
    "bulmak, levh araçlarını onlara bağlamak, hafıza kullanımını izlemek, "
    "bağlantı sorunlarını DÜZELTMEK, hafıza kalitesini korumak ve kullanıcıya "
    "Türkçe, kısa, net yanıt vermek. Sana verilen CONTEXT bloğu canlı "
    "veritabanı ve keşif verisidir; dışını tahmin etme.\n\n"
    "AKSİYON YETENEĞİN var ama DARDIR: bir aksiyon gerekiyorsa yanıtının EN "
    "SONUNA şu biçimde bir JSON bloğu ekle:\n"
    "```json\n{\"action\": {\"type\": \"...\", ...}, \"reply\": \"kullanıcıya "
    "Türkçe açıklama\"}\n```\n"
    "Aksiyon tipleri — bunlardan BAŞKASI YOKTUR:\n"
    "- {\"type\": \"add_levh_mcp\", \"agent\": \"cline\"|\"codex\"|\"claude-code\"|"
    "\"opencode\"|\"opencodex\"|\"jcode\"|\"kilo-code\"|\"oh-my-cli\"|\"gemini\"} — "
    "o ajanın config'ine levh MCP sunucusunu ekler (önce yedek alır).\n"
    "- {\"type\": \"report_finding\", \"title\": \"...\", \"detail\": \"...\", "
    "\"category\": \"bug\"|\"config\"|\"memory\"|\"agent\"|\"other\", "
    "\"severity\": \"critical\"|\"high\"|\"medium\"|\"low\"} — bulguyu gelen "
    "kutusuna yazar; kullanıcı orada görüp karar verir.\n"
    "- {\"type\": \"none\"} — aksiyon gerekmiyorsa.\n"
    "TERMINAL KOMUTU ÇALIŞTIRAMAZSIN. 'shell', 'run', 'exec' gibi bir aksiyon "
    "önerme; reddedilir. Bir sorunun terminal gerektirdiğini düşünüyorsan "
    "komutu ÇALIŞTIRMA, report_finding ile yaz ve kullanıcıya öner. "
    "Aynı aksiyonu tekrar tekrar önerme. reply alanını her zaman yaz."
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

# Ajanlar kendi adlarını tek biçimde yazmıyor: aynı Cline "cline",
# "cline-session" ve "Cline" olarak, Claude Code "claude-code" ve "Claude Code"
# olarak kaydediyor. Normalize edilmezse yazan bir ajan "sessiz" görünür —
# bekçinin tek işi buysa, yanlış alarm en pahalı çıktısıdır.
_SOURCE_ALIASES = {
    "cline-session": "cline",
    "claude code": "claude-code",
    "claudecode": "claude-code",
    "kilo": "kilo-code",
    "kilocode": "kilo-code",
    "oh-my-cli": "oh-my-cli",
}


def _normalize_source(source: str | None) -> str:
    key = (source or "").strip().lower()
    return _SOURCE_ALIASES.get(key, key)


def _silent_agents(per_source: dict) -> list[str]:
    """levh'e BAĞLI olduğu hâlde pencerede hiç yazmayan ajanlar.

    Bağlı olmayan bir ajanın sessizliği haber değil — o zaten "levh MCP yok"
    bulgusunun konusu. Haber, bağlanmış ama kullanılmayan ajan.
    """
    written = {
        _normalize_source(name)
        for name, count in per_source.items()
        if count
    }
    return [
        agent
        for agent in _ALL_AGENTS
        if describe_agent(agent)["levh_connected"] and agent not in written
    ]


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

    return {
        "window_hours": 24,
        "memories_per_source": per_source,
        "silent_agents": _silent_agents(per_source),
        "held_memories": held,
        "last_memory_at": last_mem,
    }


def scan() -> dict:
    """Tek tur keşif + izleme. Saf okuma: hiçbir şey yazmaz.

    Yazma işi çağırana ait (``record_findings``) ve çağıranın event loop'unda
    olur. Bu ayrım kasıtlı: tarama senkron olduğu için bir thread'de koşuyor,
    ve motorun paylaşılan SQLite bağlantısını o thread'den ikinci bir loop
    açarak sürmek — ``asyncio.run`` ile olsa bile — kaçınılması gereken şeydi.
    """
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "agents": discover_agents(),
        "activity": _activity_report(),
    }


# ── Aksiyon çalıştırıcı (yalnızca config kurulumu) ────────────────────


def _backup(path: Path) -> Path | None:
    """Config'i değiştirmeden önce zaman damgalı bir kopya bırak.

    Damga şart: sabit adlı tek bir ``.bak`` her çalıştırmada kendini ezerdi,
    yani ikinci bir hatalı yazımdan sonra geri dönülecek sağlam kopya kalmazdı
    — yedek almanın tek sebebi buyken.
    """
    try:
        # Mikrosaniye dahil: aynı saniye içinde iki yedek alınabiliyor ve
        # saniye çözünürlüğünde ikincisi birinciyi ezerdi.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        bak = path.with_suffix(f"{path.suffix}.{stamp}.librarian-bak")
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
    # kilo'nun OKUDUĞU dosya ~/.config/kilo/kilo.json; diğer ikisi eski
    # şemadan kalma ve kilo onlara bakmıyor, yani oradaki bir levh girdisi
    # "bağlı" demek değil. Sıralama bilerek böyle: gerçek config önce.
    "kilo-code": ("kilocode", [Path.home() / ".config" / "kilo" / "kilo.json",
                               Path.home() / ".kilocode" / "mcp.json",
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
    for name, (exe, _configs) in _ALL_AGENTS.items():
        path = shutil.which(exe)
        out.append({"agent": name, "installed": bool(path), "path": path,
                    "levh_connected": describe_agent(name)["levh_connected"]})
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


# Modelin önerebileceği aksiyonların TAMAMI. Beyaz liste, kara liste değil:
# tanınmayan her tip reddedilir, dolayısıyla yeni bir yetenek ancak buraya
# bilerek eklenerek doğar — modelin bir tip adı uydurmasıyla değil.
_ALLOWED_ACTIONS = {"add_levh_mcp", "report_finding", "none"}


async def execute_action(action: dict) -> dict:
    """LLM'in önerdiği aksiyonu çalıştırır ve sonucu döner.

    Terminal yetkisi yoktur; ``shell`` gibi bir tip gelirse çalıştırılmaz,
    reddedilir ve reddin kendisi gelen kutusuna bulgu olarak düşer — modelin
    komut çalıştırmaya çalışması, kullanıcının görmesi gereken bir olaydır.
    """
    a_type = str(action.get("type", "none"))

    if a_type not in _ALLOWED_ACTIONS:
        await _record_one(
            title=f"Librarian izinsiz aksiyon denedi: {a_type}",
            detail=(
                f"Model '{a_type}' tipinde bir aksiyon onerdi; bu tip beyaz "
                f"listede degil, calistirilmadi.\nOneri: {json.dumps(action, ensure_ascii=False)[:1000]}"
            ),
            category="agent",
            severity="high",
        )
        return {"ok": False, "msg": f"izin verilmeyen aksiyon tipi: {a_type}"}

    if a_type == "add_levh_mcp":
        agent = str(action.get("agent", ""))
        result = await asyncio.to_thread(add_levh_mcp, agent)
        await _record_one(
            title=f"{agent}: levh MCP kaydi eklendi",
            detail=f"Sonuc: {result.get('msg', '')}",
            category="config",
            severity="low",
        )
        return result

    if a_type == "report_finding":
        return await _record_one(
            title=str(action.get("title", "")),
            detail=str(action.get("detail", "")),
            category=str(action.get("category", "other")),
            severity=str(action.get("severity", "medium")),
        )

    return {"ok": True, "msg": "aksiyon gerekmedi"}


async def _connected_engine():
    """Paylaşılan motoru, DB bağlantısı kurulmuş halde döndür.

    ``initialize`` idempotent. Buradan çağrılmasının sebebi: librarian arka
    plan görevi olarak da, sohbetten de tetiklenebiliyor ve ikisi de motorun
    bağlanmasını beklemiş olmak zorunda değil — bağlanmamış bir motora yazmak
    "Database not connected" ile düşerdi.
    """
    from server.core import engine_provider

    engine = engine_provider.get_engine()
    await engine.initialize()
    return engine


async def _record_one(
    title: str, detail: str, category: str, severity: str
) -> dict:
    """Tek bir bulguyu gelen kutusuna yaz; hata sohbeti düşürmesin."""
    if not title.strip():
        return {"ok": False, "msg": "baslik bos"}
    row = build_finding_row(
        title=title, detail=detail, category=category,
        severity=severity, source=LIBRARIAN_SOURCE,
    )
    try:
        stored = await (await _connected_engine()).db.record_finding(row)
    except Exception as exc:  # noqa: BLE001
        logger.exception("librarian could not record finding")
        return {"ok": False, "msg": f"bulgu yazilamadi: {exc}"}
    return {"ok": True, "msg": f"bulgu gelen kutusuna yazildi: {stored['id']}",
            "finding_id": stored["id"]}


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


# ── Bulgu üretimi ─────────────────────────────────────────────────────


def findings_from_report(report: dict) -> list[dict]:
    """Bir tarama raporunu bulgu satırlarına çevirir.

    Her gözlem bulgu değildir. Buradan çıkan tek şey, kullanıcının bir karar
    verebileceği durumlar: bağlı olmayan bir ajan, birikmiş bir inceleme
    kuyruğu, okunamayan bir veritabanı. "Her şey yolunda" bir bulgu değildir,
    çünkü boş bir gelen kutusu zaten bunu söylüyor.

    Başlıklar sabit tutulur (sayı ve zaman başlığa girmez): parmak izi
    başlıktan üretiliyor, değişken bir başlık aynı sorunu her turda yeni bir
    satır yapardı.
    """
    out: list[dict] = []

    # Config yolu bilinmeyen ajanlar (hermes, aider) atlanır. Onlar için
    # "bağlı değil" diyemeyiz, sadece "bakamadık" diyebiliriz — ve bakamadığımız
    # şeyi bulgu diye yazmak, kullanıcının hiçbir zaman kapatamayacağı bir satır
    # üretir. Kapatılamayan bulgu, gelen kutusunun tamamını okunmaz yapar.
    unconnected = [
        a["agent"] for a in report.get("agents", [])
        if not a["levh_connected"] and a.get("configs")
    ]
    for agent in unconnected:
        out.append(
            build_finding_row(
                title=f"{agent}: levh MCP baglantisi yok",
                detail=(
                    f"'{agent}' ajaninin config dosyalarinda levh MCP kaydi bulunamadi, "
                    "yani bu ajan ortak hafizaya yazmiyor ve okumuyor.\n"
                    "Kontrol edilen dosyalar:\n"
                    + "\n".join(
                        f"  - {c['config']}"
                        for a in report.get("agents", [])
                        if a["agent"] == agent
                        for c in a.get("configs", [])
                    )
                ),
                category="config",
                severity="medium",
                source=LIBRARIAN_SOURCE,
            )
        )

    activity = report.get("activity", {})
    if activity.get("error"):
        out.append(
            build_finding_row(
                title="Hafiza veritabani okunamiyor",
                detail=f"Aktivite sorgusu basarisiz: {activity['error']}",
                category="bug",
                severity="high",
                source=LIBRARIAN_SOURCE,
            )
        )

    held = activity.get("held_memories", 0) or 0
    if held >= HELD_QUEUE_THRESHOLD:
        out.append(
            build_finding_row(
                title="held_memories kuyrugu birikti",
                detail=(
                    f"Inceleme bekleyen {held} kayit var (esik: {HELD_QUEUE_THRESHOLD}). "
                    "Bunlar kabul kapisinin 'insan karar versin' dedigi yakin "
                    "kopyalar; karara baglanmazsa hafizaya hic girmezler."
                ),
                category="memory",
                severity="low",
                source=LIBRARIAN_SOURCE,
            )
        )

    return out


async def record_findings(report: dict) -> int:
    """Rapordan çıkan bulguları gelen kutusuna yazar; yazılan satır sayısını döner.

    Motorun kendi event loop'unda çağrılır — tarama bir thread'de koşsa da
    yazma burada, çağıran loop'ta olur. Motorun paylaşılan SQLite bağlantısını
    ikinci bir loop'tan sürmek, ``asyncio.run`` ile açılan geçici bir loop
    üstünden olsa bile, kaçınılması gereken şeydi.
    """
    rows = findings_from_report(report)
    if not rows:
        return 0
    engine = await _connected_engine()
    written = 0
    for row in rows:
        try:
            await engine.db.record_finding(row)
            written += 1
        except Exception:  # noqa: BLE001 — tek bir bulgu döngüyü düşürmesin
            logger.exception("librarian could not record finding %s", row["id"])
    return written


async def run_loop(interval: int = DEFAULT_INTERVAL) -> None:
    logger.info("Librarian loop started (interval=%ss)", interval)
    while True:
        try:
            report = await asyncio.to_thread(scan)
            await record_findings(report)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("librarian scan failed")
        await asyncio.sleep(interval)


def start_background() -> asyncio.Task:
    interval = int(os.getenv("LEVH_LIBRARIAN_INTERVAL", str(DEFAULT_INTERVAL)) or DEFAULT_INTERVAL)
    return asyncio.get_running_loop().create_task(run_loop(interval))


# ── Chat ──────────────────────────────────────────────────────────────


def _context_block() -> str:
    report = scan()
    report["installed_agents"] = discover_installed()
    return "CONTEXT:\n" + json.dumps(report, ensure_ascii=False, indent=1)


def _reset_clock(response) -> str:
    """Kotanın ne zaman sıfırlanacağı — sağlayıcının söylediği biçimden okunur."""
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:  # OpenRouter epoch'u milisaniye verir, bazıları saniye
            value = float(reset)
            if value > 1e11:
                value /= 1000.0
            local = datetime.fromtimestamp(value).strftime("%H:%M")
            return f" Sifirlanma: {local}."
        except (TypeError, ValueError):
            pass
    retry_after = response.headers.get("retry-after")
    if retry_after:
        return f" {retry_after} saniye sonra tekrar denenebilir."
    return ""


def _llm_failure_reply(exc: Exception, context: str) -> str:
    """Sağlayıcı hatasını kullanıcının ne yapacağını bilebileceği bir cümleye çevir.

    429 ham hâliyle ("Client error '429 Too Many Requests'...") levh'te bir
    arıza varmış gibi görünüyordu; oysa istek sağlayıcıya ulaşıyor ve kota
    dolduğu için geri çevriliyor. Ne olduğu ve ne zaman geçeceği yazılır,
    ardından yine de işe yarayan tek şey — canlı bağlam — verilir.
    """
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        detail = ""
        try:
            body = response.json()
            detail = str(body.get("error", {}).get("message", "")).strip()
        except Exception:  # noqa: BLE001 — gövde JSON olmayabilir
            detail = ""
        head = "Model saglayicisi kotayi doldurdugumuzu soyluyor (429)."
        if detail:
            head += f" Saglayici: {detail}"
        return head + _reset_clock(response) + "\n" + context
    return f"LLM'e su an ulasamadim ({exc}).\n" + context


async def chat(question: str) -> dict:
    """Kullanıcı sorusu + canlı bağlam → LLM → (önerilen aksiyonu çalıştır) → yanıt."""
    # Tarama dosya sistemi ve SQLite'a gidiyor; bir thread'e alınmazsa
    # sorunun süresince tüm sunucunun event loop'unu bloke eder.
    context = await asyncio.to_thread(_context_block)
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        # Önceki turlar: sohbetin "hafızası" burada. Liste tutuluyor ama
        # isteğe hiç konmuyordu, yani her soru ilk soruymuş gibi cevaplanıyordu.
        *_CHAT_HISTORY,
        {"role": "user", "content": f"{context}\n\nSORU: {question}"},
    ]

    if not llm_endpoint.api_key():
        return {"answer": "LLM beyin ayarli degil (OPENAI_API_KEY yok).\n" + context,
                "backend": "offline", "actions": []}

    headers = {
        "Authorization": f"Bearer {llm_endpoint.api_key()}",
        "Content-Type": "application/json",
    }
    url = llm_endpoint.chat_completions_url()
    model = llm_endpoint.chat_model()

    executed: list[dict] = []
    reply = ""
    reached_model = True
    for _step in range(3):  # max 3 tur: düşün → aksiyon → sonuç → yanıt
        payload = {"model": model, "messages": messages, "temperature": 0.3}
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                reply = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001 — chat asla 500 dömesin
            logger.warning("librarian chat LLM failed: %s", exc)
            reply = _llm_failure_reply(exc, context)
            reached_model = False
            break

        reply_text, action = _split_reply_and_action(reply)
        if not action or action.get("type") == "none":
            reply = reply_text
            break

        result = await execute_action(action)
        executed.append({"action": action, "result": result})
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user", "content": (
            f"AKSIYON SONUCU: {json.dumps(result, ensure_ascii=False)}\n"
            "Bu sonucu kullaniciya Turkce ozetle; baska aksiyon gerekiyorsa "
            "yeni JSON blogunu ekle, gerekmiyorsa 'none' yaz."
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
    del _CHAT_HISTORY[:-CHAT_HISTORY_TURNS]
    return {
        "answer": reply,
        "backend": "llm" if reached_model else "offline",
        "actions": executed,
    }