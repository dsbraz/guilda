"""Context port (PXOS-53): retain content in the context memory. Extraction belongs to the
context memory (ADR 0001); the kernel only supplies the distribution's extraction
instructions.
"""

from typing import Protocol


class ContextStore(Protocol):
    async def retain(self, key: str, text: str, instructions: str) -> None:
        """Retain ``text`` under ``key``; retaining the same key again adds nothing."""


class FakeContextStore:
    """Records what was retained, in order."""

    def __init__(self) -> None:
        self.retained: list[tuple[str, str]] = []

    async def retain(self, key: str, text: str, instructions: str) -> None:
        self.retained.append((key, instructions))


__all__ = ["ContextStore", "FakeContextStore"]
