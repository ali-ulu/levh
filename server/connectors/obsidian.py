"""Obsidian Vault Connector — Import notes from an Obsidian vault.

Obsidian vaults are directories of markdown files with optional YAML
frontmatter and ``[[wiki-links]]``. This connector parses both and
creates structured memories.

Config keys:
    vault_path (str): Path to the Obsidian vault root.
    include_subfolders (list[str], optional): Subfolders to include (all if empty).
    exclude_subfolders (list[str], optional): Subfolders to exclude.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .base import BaseConnector

# ── Patterns ─────────────────────────────────────────────────────────

FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


class ObsidianConnector(BaseConnector):
    """Import notes from a local Obsidian vault."""

    name: str = "obsidian"
    description: str = (
        "Import notes from an Obsidian vault. Parses YAML frontmatter for tags, "
        "extracts wiki-links as relations, and uses folder structure for tagging."
    )

    def __init__(self) -> None:
        self._vault: Path | None = None
        self._include_folders: list[str] = []
        self._exclude_folders: list[str] = []

    def required_config_keys(self) -> list[str]:
        return ["vault_path"]

    async def connect(self, config: dict) -> bool:
        """Validate the vault directory exists.

        Config keys:
            vault_path (str): Path to the vault root.
            include_subfolders (list[str], optional): Only scan these folders.
            exclude_subfolders (list[str], optional): Skip these folders.
        """
        vault_path = config.get("vault_path", ".")
        self._vault = Path(vault_path).resolve()

        if not self._vault.is_dir():
            raise FileNotFoundError(f"Obsidian vault not found: {self._vault}")

        self._include_folders = config.get("include_subfolders", [])
        self._exclude_folders = config.get("exclude_subfolders", [])

        return True

    async def fetch(self, **kwargs: Any) -> list[dict]:
        """Scan the vault and return notes as memory dicts."""
        if self._vault is None:
            raise RuntimeError("Not connected. Call connect() first.")

        memories: list[dict] = []
        for md_file in self._scan_markdown():
            note_memories = self._process_note(md_file)
            memories.extend(note_memories)

        return memories

    async def disconnect(self) -> None:
        self._vault = None

    # ── Internal helpers ───────────────────────────────────────────

    def _scan_markdown(self) -> list[Path]:
        """Find all .md files respecting include/exclude filters."""
        found: list[Path] = []
        for root, dirs, files in os.walk(self._vault, topdown=True):  # type: ignore[arg-type]
            root_path = Path(root)
            rel = root_path.relative_to(self._vault)  # type: ignore[arg-type]
            dir_name = str(rel)

            # Exclude .obsidian and hidden dirs
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in self._exclude_folders
            ]

            # Filter by include list
            if self._include_folders:
                if dir_name == "." and self._include_folders:
                    # At root — only descend into included folders
                    dirs[:] = [d for d in dirs if d in self._include_folders]
                elif dir_name != "." and dir_name not in self._include_folders:
                    dirs[:] = []
                    continue

            for fname in files:
                if fname.endswith(".md"):
                    found.append(root_path / fname)

        return found

    def _process_note(self, fpath: Path) -> list[dict]:
        """Parse a single Obsidian note into a memory dict."""
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        if not raw.strip():
            return []

        # Parse frontmatter
        frontmatter, body = self._parse_frontmatter(raw)
        fm_tags = frontmatter.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [t.strip() for t in fm_tags.split(",")]
        fm_tags = [t for t in fm_tags if isinstance(t, str)]

        # Extract wiki-links as relations
        relations = WIKI_LINK_RE.findall(body)

        # Folder-based tags
        rel_path = fpath.relative_to(self._vault)  # type: ignore[arg-type]
        folder_tags = []
        if len(rel_path.parts) > 1:
            folder_tags.append(f"folder:{rel_path.parts[0]}")

        # Combine all tags
        all_tags = list(dict.fromkeys(  # deduplicate, preserve order
            fm_tags + folder_tags + ["obsidian", "note"]
        ))

        metadata: dict[str, Any] = {
            "source": "obsidian",
            "file_path": str(fpath),
            "file_name": fpath.name,
            "relative_path": str(rel_path),
            "frontmatter": frontmatter,
            "wiki_links": relations,
            "relations": relations,
        }

        # Use frontmatter title or filename
        title = frontmatter.get("title", "") or fpath.stem

        content = f"# {title}\n\n{body}".strip()

        return [{
            "content": content,
            "tags": all_tags,
            "metadata": metadata,
        }]

    @staticmethod
    def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
        """Extract YAML frontmatter and return (metadata, body)."""
        match = FRONTMATTER_RE.match(raw)
        if not match:
            return {}, raw

        fm_str = match.group(1)
        body = raw[match.end():]

        # Simple YAML parsing without PyYAML dependency
        fm: dict[str, Any] = {}
        for line in fm_str.split("\n"):
            line = line.strip()
            if ":" in line and not line.startswith("#"):
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                # Strip quotes
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                # Parse lists like [tag1, tag2]
                if value.startswith("[") and value.endswith("]"):
                    items = value[1:-1].split(",")
                    value = [item.strip().strip("'\"") for item in items if item.strip()]
                fm[key] = value

        return fm, body