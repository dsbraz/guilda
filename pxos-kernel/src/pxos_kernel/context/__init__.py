"""Context port (PXOS-53): retain, recall and forget content in the context memory.
Extraction belongs to the context memory (ADR 0001); the kernel only supplies the
distribution's extraction instructions and the access tags (ADR 0006 §1).

Recall is scoped by tags the kernel passes, never the model: only what was retained with
one of them comes back, and no tags reach nothing.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pxos_kernel.errors import PxosError


@dataclass(frozen=True)
class Recalled:
    text: str
    source: str


class ContextUnavailable(PxosError):
    """The context memory could not be reached; worth trying again."""


class ContextStore(Protocol):
    async def retain(
        self, key: str, text: str, instructions: str, *, tags: Sequence[str] = ()
    ) -> None:
        """Retain ``text`` under ``key`` with access ``tags``; it replaces what ``key`` held."""

    async def recall(self, query: str, *, tags: Sequence[str]) -> list[Recalled]:
        """What was retained with one of ``tags`` and answers ``query``, each with its key."""

    async def forget(self, source: str) -> None:
        """Purge what was retained under ``source``; forgetting it again is harmless."""


# The adapter imports the port above, so it comes last.
from pxos_kernel.context.hindsight import HindsightContextStore  # noqa: E402

__all__ = ["ContextStore", "ContextUnavailable", "HindsightContextStore", "Recalled"]
