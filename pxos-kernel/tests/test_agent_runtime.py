"""The kernel builds the agent of an approved version and runs its rounds (ADR 0006 §1, §2).

The agent gets its model from the gateway and only the tools the kernel builds. A round
reads the cycle's answers, corrections and unavailable sources as data and answers either
questions for the thread or the payload of the version's deliverable; any other answer is
refused and proposes nothing. The run is recorded once per round reference, and the
deliverable is proposed as the agent, with the run and the round as evidence.
"""

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from conftest import AGENT, RESTOCK_AGENT
from google.adk.models.llm_request import LlmRequest

from model_testing import ROUTE
from pxos_kernel.agents import (
    Agent,
    AgentVersion,
    InvalidOutput,
    MissingTool,
    NoRecordedRun,
    NotAnAgent,
    RoundInput,
    RoundOutput,
    approved_version,
    build_agent,
    propose_deliverable,
    recall_tool,
    run_round,
)
from pxos_kernel.identity import session
from pxos_kernel.model_access import FakeModels, GatewayModel, ModelGateway, ModelNotAllowed
from pxos_kernel.ontology import Registry
from pxos_kernel.state import review

ORDER = {"sku": "A-1", "quantity": 12, "occurred_at": "2026-11-14T09:00:00Z"}
DELIVERS = json.dumps({"questions": None, "deliverable": ORDER})
ASKS = json.dumps({"questions": ["Which aisle?"], "deliverable": None})
INPUT = RoundInput(
    answers=("Aisle 3.",), corrections=("Twelve, not ten.",), unavailable_sources=("shelf-log",)
)


async def no_context(query: str) -> list[tuple[str, str]]:
    return []


async def version_in_force(pool: asyncpg.Pool) -> AgentVersion:
    async with session(pool, AGENT) as connection:
        return await approved_version(connection, AGENT)


async def built(
    pool: asyncpg.Pool, registry: Registry, models: FakeModels, version: AgentVersion | None = None
) -> Agent:
    version = version or await version_in_force(pool)
    model = await ModelGateway(pool, models).adk_model()
    async with pool.acquire() as connection:
        return await build_agent(connection, model, registry, version, recall=no_context)


def declared(request: LlmRequest) -> dict[str, Any]:
    tools: list[Any] = list(request.config.tools or [])
    return {
        str(function.name): function.parameters_json_schema
        for tool in tools
        for function in tool.function_declarations or []
    }


async def test_a_round_reads_the_cycle_as_data_with_the_versions_instructions(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    models = FakeModels(reply=DELIVERS)
    agent = await built(pool, registry, models)

    assert await agent.answer(INPUT) == RoundOutput(deliverable=ORDER)
    (request,) = models.requests
    assert str(RESTOCK_AGENT["instructions"]) in str(request.config.system_instruction)
    assert json.loads(str(request.contents[-1].parts[0].text)) == {  # type: ignore[index]
        "answers": ["Aisle 3."],
        "corrections": ["Twelve, not ten."],
        "unavailable_sources": ["shelf-log"],
    }
    assert models.routes == [ROUTE]


async def test_a_round_may_ask_the_thread_instead(pool: asyncpg.Pool, registry: Registry) -> None:
    agent = await built(pool, registry, FakeModels(reply=ASKS))
    assert await agent.answer(RoundInput()) == RoundOutput(questions=("Which aisle?",))


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        json.dumps({"questions": ["Which aisle?"], "deliverable": ORDER}),
        json.dumps({"questions": [], "deliverable": None}),
        json.dumps({"questions": None, "deliverable": {**ORDER, "quantity": 0}}),
    ],
)
async def test_any_other_answer_is_refused(
    pool: asyncpg.Pool, registry: Registry, reply: str
) -> None:
    agent = await built(pool, registry, FakeModels(reply=reply))
    with pytest.raises(InvalidOutput):
        await agent.answer(RoundInput())


async def test_only_a_model_the_gateway_issued_builds_an_agent(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    models = FakeModels(reply=DELIVERS)
    version = await version_in_force(pool)
    async with pool.acquire() as connection:
        with pytest.raises(ModelNotAllowed):
            await build_agent(connection, GatewayModel(ROUTE, models(ROUTE)), registry, version)
    assert models.requests == []


async def test_only_an_agent_actor_gets_an_agent(pool: asyncpg.Pool, registry: Registry) -> None:
    person = replace(await version_in_force(pool), agent="carla")
    with pytest.raises(NotAnAgent):
        await built(pool, registry, FakeModels(reply=DELIVERS), person)


async def test_the_recall_tool_takes_only_a_query(pool: asyncpg.Pool, registry: Registry) -> None:
    """The scope of the search is bound by the kernel in the callable; the model only asks."""
    asked: list[str] = []

    async def recall(query: str) -> list[tuple[str, str]]:
        asked.append(query)
        return [("Erin restocks A-1.", "shelf-log@1")]

    assert await recall_tool(recall).func("A-1") == {
        "results": [{"text": "Erin restocks A-1.", "source": "shelf-log@1"}]
    }
    assert asked == ["A-1"]

    models = FakeModels(reply=DELIVERS)
    await (await built(pool, registry, models)).answer(RoundInput())
    assert declared(models.requests[0])["recall"]["properties"].keys() == {"query"}


async def test_the_agent_gets_only_the_tools_of_its_version(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    models = FakeModels(reply=DELIVERS)
    toolless = replace(await version_in_force(pool), tools=())
    await (await built(pool, registry, models, toolless)).answer(RoundInput())
    assert "recall" not in declared(models.requests[0])

    model = await ModelGateway(pool, models).adk_model()
    async with pool.acquire() as connection:
        with pytest.raises(MissingTool):
            await build_agent(connection, model, registry, await version_in_force(pool))


async def runs(pool: asyncpg.Pool, cycle: str) -> list[asyncpg.Record]:
    async with session(pool, AGENT) as connection:
        return await connection.fetch("SELECT * FROM pxos.agent_runs WHERE cycle = $1", cycle)


async def test_a_round_runs_once_and_a_retry_gets_what_it_answered(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    replies = iter([ASKS, DELIVERS])
    models = FakeModels(reply=lambda request: next(replies))
    agent = await built(pool, registry, models)
    for _ in range(2):
        assert await run_round(pool, agent, RoundInput(), cycle=cycle, round=1) == RoundOutput(
            questions=("Which aisle?",)
        )
    assert len(models.requests) == 1
    (run,) = await runs(pool, cycle)
    assert (run["round"], run["context_ref"]) == (1, f"{cycle}/round:1")


async def test_a_round_records_the_model_its_tokens_and_their_cost(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    agent = await built(pool, registry, FakeModels(reply=ASKS, tokens=(1_000, 50)))
    await run_round(pool, agent, RoundInput(), cycle=cycle, round=1)

    (run,) = await runs(pool, cycle)
    assert (run["model"], run["input_tokens"], run["output_tokens"]) == (ROUTE.model, 1_000, 50)
    assert run["cost"] == (1_000 * ROUTE.input_price + 50 * ROUTE.output_price) / 1_000_000


async def test_a_refused_answer_records_no_run(pool: asyncpg.Pool, registry: Registry) -> None:
    cycle = f"cycle:{uuid4()}"
    agent = await built(pool, registry, FakeModels(reply="not json"))
    with pytest.raises(InvalidOutput):
        await run_round(pool, agent, RoundInput(), cycle=cycle, round=1)
    assert await runs(pool, cycle) == []


async def test_the_database_runs_a_round_of_a_cycle_once(pool: asyncpg.Pool) -> None:
    version = await version_in_force(pool)
    insert = (
        "INSERT INTO pxos.agent_runs (definition, revision, context_ref, cycle, round)"
        " VALUES ($1, $2, 'raw', $3, 1)"
    )
    cycle = f"cycle:{uuid4()}"
    async with session(pool, AGENT) as connection:
        await connection.execute(insert, version.entity_id, version.revision, cycle)
    async with session(pool, AGENT) as connection:
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(insert, version.entity_id, version.revision, cycle)


async def test_the_agent_proposes_the_rounds_deliverable_once_for_review(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    agent = await built(pool, registry, FakeModels(reply=DELIVERS))
    await run_round(pool, agent, INPUT, cycle=cycle, round=2)

    proposal = await propose_deliverable(pool, registry, agent.version, cycle=cycle, round=2)
    again = await propose_deliverable(pool, registry, agent.version, cycle=cycle, round=2)

    (run,) = await runs(pool, cycle)
    assert again == proposal
    assert (proposal.author, proposal.action, proposal.payload) == (
        AGENT,
        "create_restock_order",
        ORDER,
    )
    assert proposal.evidence["source"] == f"agent-run:{run['id']}"
    assert proposal.evidence["context_ref"] == f"{cycle}/round:2"
    async with session(pool, "carla") as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)


async def test_the_database_takes_one_deliverable_per_run(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    agent = await built(pool, registry, FakeModels(reply=DELIVERS))
    await run_round(pool, agent, INPUT, cycle=cycle, round=1)
    proposal = await propose_deliverable(pool, registry, agent.version, cycle=cycle, round=1)
    copy = """
        INSERT INTO pxos.proposals (package, package_version, package_digest, action,
            type_name, entity_id, base_revision, valid_from, payload, evidence, digest,
            author, author_principal)
        SELECT package, package_version, package_digest, action, type_name,
            gen_random_uuid(), 0, valid_from, payload, evidence, digest, '', ''
        FROM pxos.proposals WHERE id = $1
    """
    async with session(pool, AGENT) as connection:
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(copy, proposal.id)


async def test_no_deliverable_without_a_round_that_delivered(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    cycle = f"cycle:{uuid4()}"
    version = await version_in_force(pool)
    with pytest.raises(NoRecordedRun):
        await propose_deliverable(pool, registry, version, cycle=cycle, round=1)
    await run_round(
        pool, await built(pool, registry, FakeModels(reply=ASKS)), INPUT, cycle=cycle, round=1
    )
    with pytest.raises(NoRecordedRun):
        await propose_deliverable(pool, registry, version, cycle=cycle, round=1)
