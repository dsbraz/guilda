"""Acceptance suite: one test per requirement of PRD §4 (ADR 0003 §11).

A requirement not delivered yet is an expected failure (strict), naming the issue that
delivers it. Delivering it means replacing the placeholder with the real test; a
placeholder that starts passing fails the suite.
"""

from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from mcp_testing import BASE, call, connect, running
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import build_server, http_app
from pxos_kernel.identity import session
from pxos_kernel.ontology import Registry
from pxos_kernel.state import Evidence, history, propose, review


def pending(issue: str, scope: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(raises=NotImplementedError, strict=True, reason=f"{issue}: {scope}")


async def test_req_01_same_context_for_people_and_agents(
    pool: asyncpg.Pool, registry: Registry
) -> None:
    """One MCP for people and agents: an agent acting for a person sees what the person sees,
    and whoever cannot read the type sees nothing (PXOS-58; search, pages and the API are
    still PXOS-30)."""
    async with session(pool, "erin") as connection:
        proposal = await propose(
            connection,
            registry.get("retail"),
            "create_restock_order",
            payload={"sku": "R-1", "quantity": 1, "occurred_at": "2026-11-14T09:00:00Z"},
            evidence=Evidence(source="fixture:req-01", quote="R-1", context_ref="fixture:req-01"),
            valid_from=datetime(2026, 11, 14, tzinfo=UTC),
        )
    async with session(pool, "carla") as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)

    idp = LocalIssuer.generate(issuer="http://idp.req01", audience="pxos")
    verifier = JwtVerifier(issuer=idp.issuer, audience=idp.audience, jwks=idp.jwks())
    app = http_app(
        build_server(pool, registry), verifier, issuer_url=idp.issuer, resource_url=f"{BASE}/mcp"
    )

    async def seen(person: str, agent: str | None = None) -> tuple[set[str], list[Any]]:
        token = idp.token(person, agent=agent)
        async with connect(app, token) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
        return tools, (await call(app, token, "list_restock_orders", {}))["items"]

    async with running(app):
        person_tools, person = await seen("erin")
        agent_tools, agent = await seen("erin", agent="restock-agent")
        _, no_role = await seen("frank")
        _, agent_alone = await seen("restock-agent")

    assert agent_tools == person_tools
    assert str(proposal.id) in {item["proposal_id"] for item in person}
    assert agent == person
    assert no_role == [] and agent_alone == []


@pending("PXOS-20", "ingestão automática por eventos")
def test_req_02_capture_without_discipline() -> None:
    raise NotImplementedError


async def test_req_03_every_fact_has_a_source(pool: asyncpg.Pool, registry: Registry) -> None:
    evidence = Evidence(
        source="fixture:shelf-log-2026-11-01",
        quote="Repor 3 unidades do SKU A-1",
        context_ref="fixture:ctx-req-03",
    )
    async with session(pool, "erin") as connection:
        proposal = await propose(
            connection,
            registry.get("retail"),
            "create_restock_order",
            payload={"sku": "A-1", "quantity": 3, "occurred_at": "2026-11-01T09:00:00Z"},
            evidence=evidence,
            valid_from=datetime(2026, 11, 1, tzinfo=UTC),
        )
    async with session(pool, "carla") as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
        (fact,) = await history(connection, proposal.entity_id)
    assert fact.evidence == evidence.as_dict()

    # Without a source the database refuses the proposal, whatever the application does.
    async with session(pool, "erin") as connection:
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO pxos.proposals (
                    package, package_version, package_digest, action, type_name, entity_id,
                    base_revision, operation, valid_from, payload, evidence, digest,
                    author, author_principal
                ) VALUES ($1, $2, $3, 'create_restock_order', 'RestockOrder',
                          gen_random_uuid(), 0, 'assert', now(), '{}', $4, 'x', '', '')
                """,
                proposal.package,
                proposal.package_version,
                proposal.package_digest,
                {"source": "", "quote": "q", "context_ref": "c"},
            )


@pending("PXOS-8", "instalação dedicada; credencial de outra instalação recusada")
def test_req_04_full_isolation_per_client() -> None:
    raise NotImplementedError


@pending("PXOS-9", "atores externos sem acesso ao contexto interno")
def test_req_05_identity_for_people_agents_and_external_actors() -> None:
    raise NotImplementedError


@pending("PXOS-31", "política e autonomia; ação irreversível sempre aprovada")
def test_req_06_action_with_policy_and_approval() -> None:
    raise NotImplementedError


@pending("PXOS-33", "caminho síncrono para ação pré-aprovada")
def test_req_07_action_in_conversation_time() -> None:
    raise NotImplementedError


@pending("PXOS-21", "registros da operação no mesmo estado vivo")
def test_req_08_work_and_operation_data() -> None:
    raise NotImplementedError


@pending("PXOS-37", "job diário de manutenção com fila de revisão")
def test_req_09_automatic_maintenance() -> None:
    raise NotImplementedError


@pending("PXOS-41", "resultado medido contra linha de base auditável")
def test_req_10_measured_and_auditable_result() -> None:
    raise NotImplementedError


@pending("PXOS-10, PXOS-11, PXOS-12", "pseudonimização, residência, consentimento, retenção")
def test_req_11_personal_data_protected() -> None:
    raise NotImplementedError


@pending("PXOS-45", "auditoria de portabilidade no marco do kernel")
def test_req_12_portability() -> None:
    raise NotImplementedError


@pending("PXOS-42", "distribuição de referência feita só de módulos")
def test_req_13_small_and_extensible_kernel() -> None:
    raise NotImplementedError
