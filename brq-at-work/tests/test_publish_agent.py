"""The weekly report agent is configuration of BRQ@work, published through review (ADR 0006).

A person proposes the definition file, with the file and its digest as evidence; a
platform admin approves it, and the agent then runs that version.
"""

import asyncio
import hashlib
from uuid import UUID

import pytest
import yaml

from brq_at_work.publish_agent import AGENT_FILE, main
from db_testing import Database
from pxos_kernel.agents import AgentVersion, approved_version
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Proposal, create_pool, get_proposal, review

AGENT = "weekly-report-agent"


async def approve(
    database: Database, registry: Registry, proposal_id: UUID
) -> tuple[Proposal, AgentVersion]:
    pool = await create_pool(database.app_dsn)
    try:
        async with session(pool, "eva") as connection:
            proposal = await get_proposal(connection, proposal_id)
            await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
        async with session(pool, AGENT) as connection:
            return proposal, await approved_version(connection, AGENT)
    finally:
        await pool.close()


def test_a_person_proposes_the_file_and_a_platform_admin_approves_it(
    database: Database, registry: Registry, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--dsn", database.app_dsn, "--author", "bruno"]) == 0
    proposal_id = UUID(capsys.readouterr().out.strip())
    proposal, version = asyncio.run(approve(database, registry, proposal_id))

    text = AGENT_FILE.read_text(encoding="utf-8")
    source = f"distro:{AGENT_FILE.name}@{hashlib.sha256(text.encode()).hexdigest()}"
    assert proposal.author == "bruno"
    assert proposal.evidence == {"source": source, "quote": text, "context_ref": source}
    config = yaml.safe_load(text)
    assert (version.instructions, version.memory_instructions, version.tools) == (
        config["instructions"],
        config["memory_instructions"],
        ("recall",),
    )
    assert version.deliverable == ("brq_weekly_report", "propose_weekly_report")
    assert (version.schedule, version.answer_deadline_hours, version.max_rounds) == (
        config["schedule"],
        config["answer_deadline_hours"],
        config["max_rounds"],
    )
    assert version.thread == config["thread"]
