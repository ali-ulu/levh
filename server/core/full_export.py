"""Full audit export — memories + entity graph + trust scores + conflict
candidates in one bundle, for local-first backup/auditability.

Three output shapes share one data-gathering pass:
- JSON: the raw structured bundle, machine-readable.
- SQLite: a consistent online-backup copy of the live database file.
- PDF: a short human-readable summary derived from the same JSON bundle.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1


class PdfUnavailableError(RuntimeError):
    """Raised when the optional ``fpdf2`` dependency isn't installed."""


async def build_full_export(engine: Any) -> dict:
    """Gather memories, entities, trust scores, and conflict candidates."""
    memories = await engine.export_memories()
    entities = await engine.list_entities_graph(limit=100_000)
    entity_stats = await engine.entity_graph_stats()
    trust = await engine.list_all_trust(limit=1_000_000)
    conflicts = await engine.list_conflict_candidates(status=None, limit=1000)

    return {
        "format": "levh-full-export",
        "version": FORMAT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "memories": len(memories),
            "entities": len(entities),
            "trust_scores": len(trust),
            "conflicts": len(conflicts),
        },
        "memories": memories,
        "entities": entities,
        "entity_stats": entity_stats,
        "trust": trust,
        "conflicts": conflicts,
    }


async def export_full_sqlite(engine: Any) -> bytes:
    """Return a consistent SQLite snapshot of the live database as bytes.

    Uses SQLite's online backup API (via ``Database.create_safety_backup``)
    so WAL pages are captured correctly even against a live connection.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "levh-export.sqlite"
        path = await engine.db.create_safety_backup(str(target))
        if path is None:
            raise ValueError("Cannot export an in-memory database as SQLite")
        return Path(path).read_bytes()


def _confidence_bucket(score: float) -> str:
    if score < 0.4:
        return "low (<0.4)"
    if score < 0.7:
        return "medium (0.4-0.7)"
    return "high (0.7-1.0)"


def render_full_export_pdf(export: dict) -> bytes:
    """Render a short human-readable audit report from the export bundle."""
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError as exc:
        raise PdfUnavailableError(
            "PDF export needs the optional 'fpdf2' package. "
            "Install it with:  pip install fpdf2   (or: pip install "
            "'levh[pdf]'). JSON and SQLite export work without it."
        ) from exc

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "LEVH - Full Export Audit Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Generated: {export['created_at']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    counts = export["counts"]
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    for label, key in (
        ("Memories", "memories"),
        ("Entities", "entities"),
        ("Trust scores", "trust_scores"),
        ("Conflict candidates", "conflicts"),
    ):
        pdf.cell(0, 6, f"  {label}: {counts[key]}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Entities by type", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    by_type = export.get("entity_stats", {}).get("by_type", {})
    if by_type:
        for etype, count in by_type.items():
            pdf.cell(0, 6, f"  {etype}: {count}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 6, "  (none)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Trust score distribution", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    buckets: dict[str, int] = {}
    for row in export.get("trust", []):
        buckets[_confidence_bucket(row.get("confidence", 0))] = (
            buckets.get(_confidence_bucket(row.get("confidence", 0)), 0) + 1
        )
    if buckets:
        for bucket in ("low (<0.4)", "medium (0.4-0.7)", "high (0.7-1.0)"):
            if bucket in buckets:
                pdf.cell(0, 6, f"  {bucket}: {buckets[bucket]}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 6, "  (no trust scores computed yet)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    open_conflicts = [c for c in export.get("conflicts", []) if c.get("status") == "open"]
    pdf.cell(0, 8, f"Open conflict candidates ({len(open_conflicts)})", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    if open_conflicts:
        for c in open_conflicts[:50]:
            line = f"  [{c.get('signal_type', '?')}] {c.get('memory_id_a', '')[:8]} vs {c.get('memory_id_b', '')[:8]}"
            pdf.multi_cell(0, 5, line)
        if len(open_conflicts) > 50:
            pdf.cell(0, 6, f"  ... and {len(open_conflicts) - 50} more", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 6, "  (none)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        0,
        5,
        "This PDF is a human-readable summary. The JSON or SQLite export from "
        "the same endpoint is the canonical, complete machine-readable record.",
    )

    out = pdf.output()
    return bytes(out)
