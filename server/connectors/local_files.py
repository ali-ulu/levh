"""Local Files Connector — Import local files into LEVH.

Supports: .md, .txt, .json, .py, .js, .ts, .rs, .go, .java, .yaml, .yml,
.toml, .cfg, .ini, .sh, .bash, .html, .css, .sql, .r, .jl, .ex, .exs,
.rb, .php, .c, .cpp, .h, .hpp, .cs, .swift, .kt, .scala.

Large files are chunked into smaller memories (default 2000 chars per chunk).
Each file is auto-tagged based on its extension and directory structure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import BaseConnector

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_EXTENSIONS: set[str] = {
    ".md", ".txt", ".json",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".rs", ".go", ".java",
    ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".sh", ".bash",
    ".html", ".css", ".sql",
    ".r", ".jl", ".ex", ".exs", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
}

# Extension → tag mapping
EXT_TAGS: dict[str, list[str]] = {
    ".md": ["markdown", "documentation"],
    ".txt": ["text"],
    ".json": ["json", "data"],
    ".py": ["python", "code"],
    ".js": ["javascript", "code"],
    ".ts": ["typescript", "code"],
    ".jsx": ["javascript", "react", "code"],
    ".tsx": ["typescript", "react", "code"],
    ".rs": ["rust", "code"],
    ".go": ["go", "code"],
    ".java": ["java", "code"],
    ".yaml": ["yaml", "config"],
    ".yml": ["yaml", "config"],
    ".toml": ["toml", "config"],
    ".sql": ["sql", "database"],
    ".sh": ["shell", "script"],
    ".bash": ["shell", "script"],
    ".html": ["html", "web"],
    ".css": ["css", "web", "style"],
    ".rb": ["ruby", "code"],
    ".php": ["php", "code"],
    ".swift": ["swift", "code"],
    ".kt": ["kotlin", "code"],
}

DEFAULT_CHUNK_SIZE: int = 2000
DEFAULT_OVERLAP: int = 200


class LocalFilesConnector(BaseConnector):
    """Reads local files from a directory and converts them to memories."""

    name: str = "local_files"
    description: str = (
        "Import local files (markdown, code, JSON, etc.) into memories. "
        "Recursively scans a directory, filters by extension, and chunks large files."
    )

    def __init__(self) -> None:
        self._root: Path | None = None
        self._extensions: set[str] = DEFAULT_EXTENSIONS
        self._chunk_size: int = DEFAULT_CHUNK_SIZE
        self._overlap: int = DEFAULT_OVERLAP
        self._exclude_dirs: set[str] = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            ".mypy_cache", ".pytest_cache", ".tox", "dist", "build",
            ".next", ".nuxt", ".cache", "target",
        }

    def required_config_keys(self) -> list[str]:
        return ["directory"]

    async def connect(self, config: dict) -> bool:
        """Validate the source directory exists.

        Config keys:
            directory (str): Root directory to scan.
            extensions (list[str], optional): File extensions to include.
            chunk_size (int, optional): Max chars per chunk. Default 2000.
            overlap (int, optional): Overlap between chunks. Default 200.
            exclude_dirs (list[str], optional): Directories to skip.
        """
        directory = config.get("directory", ".")
        self._root = Path(directory).resolve()

        if not self._root.is_dir():
            raise FileNotFoundError(f"Directory not found: {self._root}")

        if "extensions" in config:
            exts = config["extensions"]
            self._extensions = {e if e.startswith(".") else f".{e}" for e in exts}

        if "chunk_size" in config:
            self._chunk_size = int(config["chunk_size"])
        if "overlap" in config:
            self._overlap = int(config["overlap"])
        if self._chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if self._overlap < 0 or self._overlap >= self._chunk_size:
            raise ValueError("overlap must satisfy 0 <= overlap < chunk_size")
        if "exclude_dirs" in config:
            self._exclude_dirs = set(config["exclude_dirs"])

        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Scan the directory and return file contents as memory dicts."""
        if self._root is None:
            raise RuntimeError("Not connected. Call connect() first.")

        memories: list[dict] = []
        file_paths = self._scan_files()

        for fpath in file_paths:
            file_memories = self._process_file(fpath)
            memories.extend(file_memories)

        return memories

    async def disconnect(self) -> None:
        self._root = None

    # ── Internal helpers ───────────────────────────────────────────

    def _scan_files(self) -> list[Path]:
        """Recursively find all matching files under ``_root``."""
        found: list[Path] = []
        for root, dirs, files in os.walk(self._root, topdown=True):
            root_path = Path(root)
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in self._exclude_dirs]
            for fname in files:
                fpath = root_path / fname
                if fpath.suffix.lower() not in self._extensions:
                    continue
                try:
                    resolved = fpath.resolve(strict=True)
                    resolved.relative_to(self._root)
                except (OSError, ValueError):
                    # Never follow a symlinked file outside the configured root.
                    continue
                if resolved.is_file():
                    found.append(resolved)
        return found

    def _process_file(self, fpath: Path) -> list[dict]:
        """Read a file and chunk it into memory-compatible dicts."""
        try:
            resolved = fpath.resolve(strict=True)
            resolved.relative_to(self._root)  # type: ignore[arg-type]
            raw = resolved.read_text(encoding="utf-8", errors="replace")
            fpath = resolved
        except (OSError, ValueError):
            return []

        if not raw.strip():
            return []

        ext = fpath.suffix.lower()
        tags = list(EXT_TAGS.get(ext, [ext.lstrip(".")]))

        # Add directory-based tags (up to 2 levels)
        rel = fpath.relative_to(self._root)  # type: ignore[arg-type]
        parts = rel.parts
        if len(parts) > 1:
            tags.append(str(parts[0]))
        if len(parts) > 2:
            tags.append(f"{parts[0]}/{parts[1]}")

        metadata: dict[str, Any] = {
            "source": "local_files",
            "file_path": str(fpath),
            "file_name": fpath.name,
            "extension": ext,
            "relative_path": str(rel),
        }

        # Try parsing JSON files specially
        if ext == ".json":
            return self._process_json_file(raw, tags, metadata)

        # Chunk if large
        if len(raw) <= self._chunk_size:
            return [{
                "content": raw.strip(),
                "tags": tags,
                "metadata": metadata,
            }]

        return self._chunk_text(raw, tags, metadata, fpath.name)

    def _process_json_file(
        self, raw: str, tags: list[str], metadata: dict[str, Any]
    ) -> list[dict]:
        """Flatten JSON into readable text memories."""
        import json

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return [{
                "content": raw.strip()[:self._chunk_size],
                "tags": tags + ["json-parse-error"],
                "metadata": metadata,
            }]

        if isinstance(data, list):
            text = "\n".join(
                f"- {json.dumps(item, ensure_ascii=False)}" for item in data
            )
        elif isinstance(data, dict):
            lines = []
            for k, v in data.items():
                lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
            text = "\n".join(lines)
        else:
            text = str(data)

        return [{
            "content": text.strip()[:self._chunk_size * 2],
            "tags": tags,
            "metadata": {**metadata, "json_keys": list(data.keys()) if isinstance(data, dict) else None},
        }]

    def _chunk_text(
        self,
        text: str,
        tags: list[str],
        metadata: dict[str, Any],
        file_name: str,
    ) -> list[dict]:
        """Split large text into overlapping chunks."""
        chunks: list[dict] = []
        size = self._chunk_size
        overlap = self._overlap
        start = 0
        chunk_idx = 0

        while start < len(text):
            end = start + size
            # Try to break at a newline near the end
            if end < len(text):
                nl = text.rfind("\n", start + size // 2, end)
                if nl > start:
                    end = nl + 1

            chunk_text = text[start:end].strip()
            if chunk_text:
                chunk_meta = {**metadata, "chunk_index": chunk_idx}
                chunks.append({
                    "content": chunk_text,
                    "tags": tags + [f"chunk-{chunk_idx}"],
                    "metadata": chunk_meta,
                })
                chunk_idx += 1

            next_start = end - overlap
            # Defensive progress invariant: even malformed future callers can
            # never make the chunker loop at the same cursor forever.
            start = end if next_start <= start else next_start

        return chunks