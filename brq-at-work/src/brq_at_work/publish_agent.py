"""Propose the weekly report agent's definition file as its next version (ADR 0006 §5).

A person proposes it; a platform admin approves it through the review cycle. The file
and its digest are the evidence, so the approved version points to what was reviewed.

    uv run python -m brq_at_work.publish_agent --dsn DSN --author ACTOR [FILE]
"""

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import yaml

from brq_at_work.composition import ROOT, build_registry
from pxos_kernel.agents import propose_version
from pxos_kernel.state import Evidence, Proposal, create_pool

AGENT_FILE = ROOT / "weekly_report_agent.yaml"


async def publish(dsn: str, path: Path, *, author: str) -> Proposal:
    text = path.read_text(encoding="utf-8")
    source = f"distro:{path.name}@{hashlib.sha256(text.encode()).hexdigest()}"
    pool = await create_pool(dsn)
    try:
        return await propose_version(
            pool,
            build_registry(),
            yaml.safe_load(text),
            author=author,
            evidence=Evidence(source=source, quote=text, context_ref=source),
        )
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="the installation's application database")
    parser.add_argument("--author", required=True, help="the person who proposes it")
    parser.add_argument("file", nargs="?", type=Path, default=AGENT_FILE)
    args = parser.parse_args(argv)
    proposal = asyncio.run(publish(args.dsn, args.file, author=args.author))
    print(proposal.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
