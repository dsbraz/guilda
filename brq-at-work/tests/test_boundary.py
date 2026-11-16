"""BRQ@work builds no agent of its own (ADR 0004 §2, rule 1).

The kernel builds every agent, on the gateway's model; a distribution gives configuration
and, at most, tool code. So nothing here imports ADK or a model provider's client.
"""

import re
from pathlib import Path

import brq_at_work

FORBIDDEN = re.compile(
    r"^\s*(import|from)\s+(google\.adk|google\.genai|google(\s+import\s+(adk|genai))"
    r"|anthropic|vertexai)\b",
    re.M,
)


def test_the_distribution_imports_no_agent_runtime_or_model_client() -> None:
    files = list(Path(brq_at_work.__file__).parent.rglob("*.py"))
    assert files and not [path for path in files if FORBIDDEN.search(path.read_text())]
