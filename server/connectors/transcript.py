"""Transcript Connector — Import meeting transcripts as memories.

The third work-life capture source (roadmap Phase 1), completing the trio:
Calendar (when/who) + Email (correspondence) + Transcript (what was actually
said). Later you can ask "what did we decide in the roadmap call?" or "what did
Dana commit to in that meeting?".

Parses the transcript/caption formats that Zoom, Google Meet, Teams, Otter,
Fireflies, and Whisper export — all offline, with a small dependency-free
parser:
  - ``.vtt`` (WebVTT) — captions with ``HH:MM:SS.mmm --> …`` cues and optional
    ``<v Speaker>`` voice tags.
  - ``.srt`` (SubRip) — numbered blocks with ``HH:MM:SS,mmm --> …`` timings.
  - ``.txt`` — plain transcript, optionally ``Speaker: text`` per line.

A transcript can be thousands of lines, so each meeting becomes ONE summarized
memory (not one per line) — reusing the project summarizer: an LLM when
``OPENAI_API_KEY`` is set, else a deterministic offline extractive summary.
The full transcript excerpt and the speaker list are kept in metadata.

Config keys (one of transcript_path / transcript_dir required):
    transcript_path (str): Path to a .vtt/.srt/.txt transcript file.
    transcript_dir (str):  Folder of transcript files (each → one memory).
    meeting_title (str, optional): Title for a single-file import.
    summarize (bool, optional): Summarize into one memory (default True). When
        False, the memory holds the cleaned full transcript instead.
    max_chars (int, optional): Cap on stored transcript excerpt (default 4000).
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

from .base import BaseConnector

# Timestamp cue lines for VTT ("-->" with dots) and SRT (commas).
_CUE_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->")
_VTT_VOICE_RE = re.compile(r"<v\s+([^>]+)>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SPEAKER_PREFIX_RE = re.compile(r"^([A-Z][\w .'-]{0,40}):\s+(.*)$")


def _clean_line(line: str) -> str:
    return _TAG_RE.sub("", line).strip()


def parse_transcript(text: str) -> dict:
    """Parse a .vtt/.srt/.txt transcript into speakers + ordered text lines.

    Returns {"speakers": [...], "lines": ["Speaker: text", ...],
    "text": "joined"}. Deterministic; never raises."""
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    speakers: list[str] = []
    lines: list[str] = []

    def _add_speaker(name: str) -> None:
        name = name.strip()
        if name and name not in speakers:
            speakers.append(name)

    for line in raw:
        s = line.strip()
        if not s:
            continue
        up = s.upper()
        # Skip format scaffolding.
        if up == "WEBVTT" or up.startswith("NOTE "):
            continue
        if _CUE_RE.search(s):
            continue
        if s.isdigit():  # SRT sequence number
            continue

        # VTT voice tag: <v Speaker>text
        voice = _VTT_VOICE_RE.search(s)
        if voice:
            speaker = voice.group(1).strip()
            _add_speaker(speaker)
            content = _clean_line(_VTT_VOICE_RE.sub("", s))
            if content:
                lines.append(f"{speaker}: {content}")
            continue

        cleaned = _clean_line(s)
        if not cleaned:
            continue

        # "Speaker: text" prefix (common in Otter/plain transcripts)
        m = _SPEAKER_PREFIX_RE.match(cleaned)
        if m:
            _add_speaker(m.group(1))
            lines.append(cleaned)
        else:
            lines.append(cleaned)

    return {"speakers": speakers, "lines": lines, "text": "\n".join(lines)}


class TranscriptConnector(BaseConnector):
    """Import meeting transcripts (.vtt/.srt/.txt) as summarized memories."""

    name: str = "transcript"
    description: str = (
        "Import meeting transcripts (.vtt / .srt / .txt) from Zoom, Google Meet, "
        "Teams, Otter, Fireflies, or Whisper. Each meeting becomes one memory — "
        "summarized (LLM if OPENAI_API_KEY is set, offline otherwise) with the "
        "speaker list and a transcript excerpt in metadata. Fully offline, no keys."
    )

    def __init__(self) -> None:
        self._files: list[tuple[str, str]] = []  # (title, raw_text)
        self._summarize: bool = True
        self._max_chars: int = 4000

    def required_config_keys(self) -> list[str]:
        return ["transcript_path"]

    def help_text(self) -> str:
        return (
            "Connector: transcript\n"
            "  Import meeting transcripts (.vtt/.srt/.txt) as summarized memories.\n"
            "  Provide ONE of:\n"
            "    transcript_path : path to a .vtt/.srt/.txt file\n"
            "    transcript_dir  : folder of transcript files (each → one memory)\n"
            "  Optional: meeting_title, summarize (default true), max_chars.\n"
            "  Tip: Zoom/Meet/Teams save .vtt captions; Otter/Fireflies export .txt."
        )

    async def connect(self, config: dict) -> bool:
        path = config.get("transcript_path", "").strip()
        directory = config.get("transcript_dir", "").strip()
        title = config.get("meeting_title", "").strip()
        self._summarize = bool(config.get("summarize", True))
        self._max_chars = int(config.get("max_chars", 4000) or 4000)

        self._files = []
        exts = (".vtt", ".srt", ".txt")
        if path:
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Transcript file not found: {path}")
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                name = title or os.path.splitext(os.path.basename(path))[0]
                self._files.append((name, f.read()))
        elif directory:
            if not os.path.isdir(directory):
                raise FileNotFoundError(f"Transcript directory not found: {directory}")
            for fn in sorted(os.listdir(directory)):
                if not fn.lower().endswith(exts):
                    continue
                with open(os.path.join(directory, fn), "r", encoding="utf-8", errors="replace") as f:
                    self._files.append((os.path.splitext(fn)[0], f.read()))
        else:
            raise ValueError(
                "Transcript connector needs 'transcript_path' or 'transcript_dir'."
            )
        return True

    async def _to_memory(self, title: str, raw: str) -> Optional[dict]:
        parsed = parse_transcript(raw)
        lines = parsed["lines"]
        if not lines:
            return None
        speakers = parsed["speakers"]

        if self._summarize:
            from server.core.summarizer import summarize_texts

            # "auto" uses an LLM only when OPENAI_API_KEY is set; otherwise a
            # deterministic offline extractive summary (test-safe).
            body = await summarize_texts(lines, mode="auto")
            body_label = "Summary"
        else:
            body = parsed["text"][: self._max_chars]
            body_label = "Transcript"

        parts = [f"Meeting transcript: {title}"]
        if speakers:
            parts.append(f"Participants: {', '.join(speakers)}")
        parts.append(f"{body_label}:\n{body}")
        content = "\n".join(parts)

        metadata: dict[str, Any] = {
            "meeting_title": title,
            "speakers": speakers,
            "line_count": len(lines),
            "transcript_excerpt": parsed["text"][: self._max_chars],
            "summarized": self._summarize,
        }
        return {"content": content, "tags": ["meeting", "transcript"], "metadata": metadata}

    async def fetch(self, **kwargs: Any) -> list[dict]:
        if not self._files:
            raise RuntimeError("Not connected. Call connect() first.")
        memories: list[dict] = []
        for title, raw in self._files:
            mem = await self._to_memory(title, raw)
            if mem:
                memories.append(mem)
        return memories

    async def disconnect(self) -> None:
        self._files = []
