"""MCP local da BRQ@work (fatia 3), com o IdP de desenvolvimento.

Na raiz do repositório, com o Postgres do Compose no ar:

    PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py serve
    PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py token ana
    PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py token bruno --agent meeting-agent
    PYTHONPATH=. uv run python brq-at-work/examples/mcp_local.py approve <proposal_id> --as ana

``serve`` recria o banco ``pxos_mcp`` com um compromisso já aprovado e sobe o MCP em
http://127.0.0.1:18000/mcp. ``token`` emite um token de 12 horas assinado pela chave de
desenvolvimento (em ``.secrets/``, fora do git). ``approve`` faz o papel do card: ainda
não há canal real, então a aprovação é feita aqui, como o ator informado.
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import asyncpg
import uvicorn

from brq_at_work.composition import bootstrap, build_registry
from db_testing import create_test_database, existing_database
from pxos_kernel.api.auth import JwtVerifier, LocalIssuer
from pxos_kernel.api.server import build_server, http_app
from pxos_kernel.errors import PxosError
from pxos_kernel.identity import session
from pxos_kernel.state import Evidence, create_pool, get_proposal, propose, review

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / ".secrets" / "dev-idp.pem"
ISSUER = "http://127.0.0.1/dev-idp"
AUDIENCE = "brq-at-work"
DATABASE = "pxos_mcp"
PORT = int(os.environ.get("PXOS_MCP_PORT", "18000"))


def issuer() -> LocalIssuer:
    if not KEY.exists():
        KEY.parent.mkdir(exist_ok=True)
        KEY.write_bytes(LocalIssuer.generate(issuer=ISSUER, audience=AUDIENCE).pem())
        KEY.chmod(0o600)
    return LocalIssuer.from_pem(KEY.read_bytes(), issuer=ISSUER, audience=AUDIENCE)


async def seed(pool: asyncpg.Pool) -> None:
    registry = build_registry()
    async with session(pool, "scribe-agent") as connection:
        proposal = await propose(
            connection,
            registry.get("work_context"),
            "create_commitment",
            payload={
                "description": "Entregar o protótipo do onboarding",
                "owner": "bruno",
                "due_date": "2026-11-28",
                "work_stream": "onboarding-app",
            },
            evidence=Evidence(
                source="fixture:transcript/2026-11-12-status",
                quote="Bruno: fechado, eu entrego o protótipo do onboarding até sexta, dia 28.",
                context_ref="fixture:transcript/2026-11-12-status@1",
            ),
            valid_from=datetime(2026, 11, 12, 14, tzinfo=UTC),
        )
    async with session(pool, "ana") as connection:
        await review(connection, registry, proposal.id, "approved", digest=proposal.digest)


def serve() -> None:
    registry = build_registry()
    database = create_test_database(DATABASE)
    bootstrap(database.owner_dsn, registry)
    idp = issuer()

    async def main() -> None:
        pool = await create_pool(database.app_dsn)
        await seed(pool)
        app = http_app(
            build_server(pool, registry, name="brq-at-work"),
            JwtVerifier(issuer=ISSUER, audience=AUDIENCE, jwks=idp.jwks()),
            issuer_url=ISSUER,
            resource_url=f"http://127.0.0.1:{PORT}/mcp",
        )
        print(f"MCP em http://127.0.0.1:{PORT}/mcp (banco {DATABASE}, recriado)")
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT))
        try:
            await server.serve()
        finally:
            await pool.close()

    asyncio.run(main())


async def approve(proposal_id: UUID, actor: str) -> None:
    registry = build_registry()
    pool = await create_pool(existing_database(DATABASE).app_dsn)
    try:
        async with session(pool, actor) as connection:
            proposal = await get_proposal(connection, proposal_id)
            await review(connection, registry, proposal.id, "approved", digest=proposal.digest)
        print(f"{proposal_id} aprovada por {actor}")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve")
    token = commands.add_parser("token")
    token.add_argument("person")
    token.add_argument("--agent")
    approval = commands.add_parser("approve")
    approval.add_argument("proposal_id", type=UUID)
    approval.add_argument("--as", dest="actor", required=True)
    args = parser.parse_args()
    if args.command == "serve":
        serve()
    elif args.command == "token":
        print(issuer().token(args.person, agent=args.agent, ttl=12 * 3600))
    else:
        try:
            asyncio.run(approve(args.proposal_id, args.actor))
        except PxosError as error:
            raise SystemExit(f"recusado: {error}") from None


if __name__ == "__main__":
    main()
