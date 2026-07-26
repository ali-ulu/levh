"""Short-Term Memory — In-memory FIFO deque for recent context window."""

from __future__ import annotations

from collections import deque
from typing import Optional

from .types import Memory, MemoryType


class ShortTermMemory:
    """FIFO deque with a max size. Oldest memories are automatically evicted."""

    def __init__(self, max_size: int = 50):
        self._deque: deque[Memory] = deque(maxlen=max_size)
        self.max_size = max_size

    def add(self, memory: Memory) -> Memory:
        self._deque.append(memory)
        return memory

    def get_all(self) -> list[Memory]:
        return list(self._deque)

    def get_recent(self, n: int = 10) -> list[Memory]:
        items = list(self._deque)
        return items[-n:] if n > 0 else items

    def find(self, memory_id: str) -> Optional[Memory]:
        for m in self._deque:
            if m.id == memory_id:
                return m
        return None

    def remove(self, memory_id: str) -> bool:
        for i, m in enumerate(self._deque):
            if m.id == memory_id:
                del self._deque[i]
                return True
        return False

    def clear(self) -> int:
        count = len(self._deque)
        self._deque.clear()
        return count

    def __len__(self) -> int:
        return len(self._deque)

    def __bool__(self) -> bool:
        return len(self._deque) > 0
