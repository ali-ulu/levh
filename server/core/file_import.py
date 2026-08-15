"""Turn an uploaded file's raw bytes into memory-sized text chunks.

Plain text formats (txt/md/csv/json/log and anything else that decodes as
UTF-8/latin-1) work with the standard library alone. Richer formats degrade
gracefully: if the optional extractor package isn't installed, the file still
gets a memory recording that it arrived, instead of failing the upload.
"""

from __future__ import annotations

import io
import os
import zipfile

CHUNK_CHARS = 3000

# Extensions read as plain text, in the order attempted for decoding.
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".html", ".htm", ".xml"}

_TEXT_DECODINGS = ("utf-8", "utf-16", "latin-1")


def _looks_binary(blob: bytes) -> bool:
    """Heuristic: NUL bytes, or too many non-printable bytes, mean 'not text'."""
    sample = blob[:8192]
    if b"\x00" in sample:
        return True
    if not sample:
        return False
    printable = sum(1 for b in sample if b in (9, 10, 13) or 0x20 <= b < 0x7F or b >= 0x80)
    return printable / len(sample) < 0.85


def _decode_text(blob: bytes) -> str | None:
    if _looks_binary(blob):
        return None
    for enc in _TEXT_DECODINGS:
        try:
            return blob.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _extract_pdf(blob: bytes) -> tuple[str | None, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "PDF text extraction needs the optional 'pypdf' package (pip install levh[files])"
    try:
        reader = PdfReader(io.BytesIO(blob))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(pages).strip(), None
    except Exception as exc:  # pragma: no cover - depends on malformed PDFs
        return None, f"could not parse PDF: {exc}"


def _extract_docx(blob: bytes) -> tuple[str | None, str | None]:
    try:
        import docx
    except ImportError:
        return None, "Word text extraction needs the optional 'python-docx' package (pip install levh[files])"
    try:
        doc = docx.Document(io.BytesIO(blob))
        return "\n".join(p.text for p in doc.paragraphs).strip(), None
    except Exception as exc:  # pragma: no cover - depends on malformed docx
        return None, f"could not parse Word document: {exc}"


def _extract_xlsx(blob: bytes) -> tuple[str | None, str | None]:
    try:
        import openpyxl
    except ImportError:
        return None, "Excel text extraction needs the optional 'openpyxl' package (pip install levh[files])"
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
        sheets = []
        for ws in wb.worksheets:
            rows = [
                "\t".join("" if c is None else str(c) for c in row)
                for row in ws.iter_rows(values_only=True)
            ]
            sheets.append(f"# {ws.title}\n" + "\n".join(rows))
        return "\n\n".join(sheets).strip(), None
    except Exception as exc:  # pragma: no cover - depends on malformed xlsx
        return None, f"could not parse Excel workbook: {exc}"


_BY_SUFFIX = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".xlsx": _extract_xlsx,
    ".xlsm": _extract_xlsx,
}


def extract_parts(filename: str, blob: bytes) -> tuple[list[tuple[str, str]], list[str]]:
    """Return ``[(label, text), ...]`` plus any non-fatal warnings.

    A ``.zip`` yields one part per contained file (non-recursive: nested
    archives are skipped and reported as a warning). Anything with no known
    extractor and no decodable text produces no parts but a warning, so the
    caller can still record that the file arrived.
    """

    suffix = os.path.splitext(filename)[1].lower()
    warnings: list[str] = []

    if suffix == ".zip":
        parts: list[tuple[str, str]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = info.filename
                    base = os.path.basename(name)
                    if not base or base.startswith("."):
                        continue
                    if name.lower().endswith(".zip"):
                        warnings.append(f"{name}: nested zip archives are not expanded")
                        continue
                    inner = zf.read(info)
                    inner_parts, inner_warnings = extract_parts(name, inner)
                    parts.extend((f"{name} :: {label}" if label else name, text) for label, text in inner_parts)
                    warnings.extend(f"{name}: {w}" for w in inner_warnings)
        except zipfile.BadZipFile:
            warnings.append("not a valid zip archive")
        return parts, warnings

    if suffix in _BY_SUFFIX:
        text, warning = _BY_SUFFIX[suffix](blob)
        if warning:
            warnings.append(warning)
        return ([(filename, text)] if text else []), warnings

    if suffix in TEXT_SUFFIXES:
        text = _decode_text(blob)
        if text is None:
            warnings.append("could not decode as text")
            return [], warnings
        return [(filename, text.strip())], warnings

    # Unknown extension: try plain text as a last resort before giving up.
    text = _decode_text(blob)
    if text is not None and text.strip():
        return [(filename, text.strip())], warnings
    warnings.append(f"no text extractor for '{suffix or 'unknown'}' files — content not indexed")
    return [], warnings


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph boundaries into chunks no larger than ``chunk_chars``."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > chunk_chars:
            chunks.append(current)
            current = para
        elif len(para) > chunk_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(para), chunk_chars):
                chunks.append(para[i : i + chunk_chars])
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks
