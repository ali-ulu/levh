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

Yapı (issue #98): tek dosya dokuz sorumluluk taşıyordu; artık sorumluluk
başına bir modül var ve bu ``__init__`` yalnızca orkestrasyon + eski import
yüzeyini koruma işi görür. ``from server.core import librarian`` kullanan
her yer (route'lar, testler) değişmeden çalışmaya devam eder.
"""

from __future__ import annotations

# Eski tek-dosya modülünün ad alanını paylaşan testler, ``librarian.httpx``
# ve ``librarian.shutil`` üzerinden monkeypatch yapıyor; paket ``__init__``
# bu iki adı da taşımaya devam eder ki monkeypatch gerçek kullanım noktasına
# (chat'teki httpx, config'teki shutil) etki etsin.
from . import chat as _chat_mod, config as _config_mod
# Monkeypatch uyumluluğu: ``librarian.httpx.AsyncClient`` yaması chat.py'nin
# gördüğü httpx ile aynı modül objesi olmalı (chat.py bu paketin httpx'ini
# kullanır); ``librarian.shutil.which`` yaması da config.py'ninki ile aynı olmalı.
httpx = _chat_mod.httpx
shutil = _config_mod.shutil

# Eski modül yüzeyi — hepsi sorumluluk modüllerinden yeniden dışa aktarılır.
from .activity import (  # noqa: F401
    _activity_report,
    _normalize_source,
    _silent_agents,
    scan,
)
from .actions import (  # noqa: F401
    _ALLOWED_ACTIONS,
    _memory_analysis,
    _parse_json_block,
    _split_reply_and_action,
    execute_action,
)
from .chat import (  # noqa: F401
    _CHAT_HISTORY,
    CHAT_HISTORY_TURNS,
    _SYSTEM_PROMPT,
    _context_block,
    _llm_failure_reply,
    _reset_clock,
    chat,
)
from .config import (  # noqa: F401
    _backup,
    _levh_executable_fallback,
    add_levh_mcp,
)
from .db import (  # noqa: F401
    db_path as _db_path,
    ro_conn as _ro_conn,
)
from .discovery import (  # noqa: F401
    _ALL_AGENTS,
    _has_levh,
    describe_agent,
    discover_agents,
    discover_installed,
)
from .findings import (  # noqa: F401
    HELD_QUEUE_THRESHOLD,
    LIBRARIAN_SOURCE,
    findings_from_report,
    record_findings,
)
from .loop import (  # noqa: F401
    DEFAULT_INTERVAL,
    run_loop,
    start_background,
)
