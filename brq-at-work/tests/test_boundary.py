"""BRQ@work builds no agent of its own (ADR 0004 §2, rule 1).

The kernel builds every agent, on the gateway's model, and runs the agent's cycle; a
distribution gives configuration and, at most, tool code. So nothing here imports ADK, a
model provider's client or Temporal (ADR 0006 §2).
"""

import re
from pathlib import Path

import brq_at_work

FORBIDDEN = re.compile(
    r"^\s*(import|from)\s+(google\.adk|google\.genai|google(\s+import\s+(adk|genai))"
    r"|anthropic|vertexai|temporalio)\b",
    re.M,
)


def test_the_distribution_imports_no_agent_runtime_model_client_or_workflow_engine() -> None:
    assert FORBIDDEN.search("from temporalio import workflow")
    files = list(Path(brq_at_work.__file__).parent.rglob("*.py"))
    assert files and not [path for path in files if FORBIDDEN.search(path.read_text())]
