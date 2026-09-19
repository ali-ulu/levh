"""Finding generation and recording.

Split out of the single-file librarian (issue #98): turning a scan report
into finding rows, and writing them into the findings inbox through the
connected engine.
"""

from __future__ import annotations

import logging

from server.core.findings import build_row as build_finding_row

logger = logging.getLogger("levh.librarian")

LIBRARIAN_SOURCE = "librarian"
# Kuyruktaki birkaç kayıt normal çalışmadır; bulgu olması için birikmesi gerek.
HELD_QUEUE_THRESHOLD = 20


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
    except Exception as exc:
        logger.exception("librarian could not record finding")
        return {"ok": False, "msg": f"bulgu yazilamadi: {exc}"}
    return {"ok": True, "msg": f"bulgu gelen kutusuna yazildi: {stored['id']}",
            "finding_id": stored["id"]}


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
        except Exception:
            logger.exception("librarian could not record finding %s", row["id"])
    return written
