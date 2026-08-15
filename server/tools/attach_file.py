"""Tool: attach_file — bind a local file to a memory as evidence, not content."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from server.core.memory_engine import MemoryEngine


def register(mcp: FastMCP, engine: MemoryEngine) -> None:
    @mcp.tool()
    async def attach_file(
        memory_id: str,
        path: str,
        derived_text: str = "",
        derived_by: str = "manual",
    ) -> str:
        """Attach a local file (screenshot, PDF, recording, design file, ...)
        to an existing memory as evidence.

        The memory stays text — decay and recall keep working exactly as
        before. The file is referenced by its local path and a sha256 taken
        right now, so a later ``verify`` can tell if it moved or changed.
        Recall searches ``derived_text`` (OCR/transcript/caption), not the
        file itself, so pass it if you already have one — an offline
        extractor's output or your own description of what the file shows.

        Args:
            memory_id: ID of the memory to attach the file to.
            path: Absolute local path to the file.
            derived_text: Optional text describing/transcribing the file's
                content, so recall can find it.
            derived_by: Who/what produced derived_text — "manual" (typed by
                a human), "tesseract", "whisper", or "none".
        """
        try:
            attachment = await engine.attach_file(
                memory_id, path, derived_text=derived_text or None, derived_by=derived_by
            )
        except ValueError as exc:
            return str(exc)
        return (
            f"Attached {path}\n"
            f"  Attachment ID: {attachment['id']}\n"
            f"  Memory ID: {memory_id}\n"
            f"  sha256: {attachment['sha256'][:16]}...\n"
            f"  Size: {attachment['size']} bytes | MIME: {attachment['mime'] or 'unknown'}"
        )
