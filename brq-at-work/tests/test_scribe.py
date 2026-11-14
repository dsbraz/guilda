"""BRQ@work scribe (PXOS-56, PXOS-80): the draft of its definition."""

import re
from pathlib import Path

import brq_at_work
from brq_at_work.composition import build_registry
from brq_at_work.scribe import DRAFT, MEMORY_INSTRUCTIONS
from pxos_kernel.agents import AGENTS


def test_the_draft_is_a_valid_definition_of_the_commitment_action() -> None:
    registry = build_registry()
    assert registry.get(AGENTS).validate("AgentVersion", DRAFT) == DRAFT
    package, action = DRAFT["proposes"].split(".")
    assert registry.get(package).package.action(action).creates == "Commitment"


def test_memory_instructions_ask_for_the_verbatim_quote() -> None:
    assert "COMPROMISSO | dono=" in MEMORY_INSTRUCTIONS
    assert "literal" in MEMORY_INSTRUCTIONS


def test_the_distribution_builds_no_adk_agent() -> None:
    """Only the kernel builds agents, on the gateway's model (ADR 0004 §2, rule 1)."""
    source = Path(brq_at_work.__file__).parent
    adk = re.compile(r"^\s*(import|from)\s+google\.adk\b", re.M)
    files = list(source.rglob("*.py"))
    assert files and not [path for path in files if adk.search(path.read_text())]
