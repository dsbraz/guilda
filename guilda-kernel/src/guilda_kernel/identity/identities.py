"""Fixed identities loaded from configuration (K51).

People, external actors (customers, suppliers) and agents are actors with roles; a
delegation lets an agent act on behalf of an internal person with only that person's
roles. The database refuses any session of an external actor (K61). An actor may be
bound to its identities on a channel, so a message or a click there names the actor. The
owner connection writes them at bootstrap; the application role can only read them.
"""

from pathlib import Path
from typing import Literal

import asyncpg
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from guilda_kernel.errors import GuildaError
from guilda_kernel.identity.session import Connection, NotAuthorized


class InvalidIdentities(GuildaError):
    """The identity configuration is inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Actor(_Strict):
    id: str
    kind: Literal["internal_person", "external_person", "agent"]
    name: str
    roles: tuple[str, ...] = ()
    channel_ids: tuple[str, ...] = ()


class Delegation(_Strict):
    agent: str
    person: str


class Identities(_Strict):
    actors: tuple[Actor, ...]
    delegations: tuple[Delegation, ...] = ()


def read_identities(path: Path) -> Identities:
    try:
        identities = Identities.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as error:
        raise InvalidIdentities(f"{path}: {error}") from None
    kinds = {actor.id: actor.kind for actor in identities.actors}
    if len(kinds) != len(identities.actors):
        raise InvalidIdentities(f"{path}: duplicated actor ids")
    for delegation in identities.delegations:
        if kinds.get(delegation.agent) != "agent":
            raise InvalidIdentities(f"{path}: {delegation.agent} is not an agent")
        if kinds.get(delegation.person) != "internal_person":
            raise InvalidIdentities(f"{path}: {delegation.person} is not a person")
    return identities


async def install_identities(owner: asyncpg.Connection, identities: Identities) -> None:
    """Upsert actors and replace roles and delegations, in one transaction."""
    async with owner.transaction():
        await owner.executemany(
            """
            INSERT INTO guilda.actors (id, kind, name) VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name
            """,
            [(actor.id, actor.kind, actor.name) for actor in identities.actors],
        )
        await owner.execute("DELETE FROM guilda.actor_roles")
        await owner.executemany(
            "INSERT INTO guilda.actor_roles (actor_id, role) VALUES ($1, $2)",
            [(actor.id, role) for actor in identities.actors for role in actor.roles],
        )
        await owner.execute("DELETE FROM guilda.delegations")
        await owner.executemany(
            "INSERT INTO guilda.delegations (agent_id, person_id) VALUES ($1, $2)",
            [(item.agent, item.person) for item in identities.delegations],
        )
        await owner.execute("DELETE FROM guilda.channel_identities")
        await owner.executemany(
            "INSERT INTO guilda.channel_identities (channel_id, actor_id) VALUES ($1, $2)",
            [
                (channel_id, actor.id)
                for actor in identities.actors
                for channel_id in actor.channel_ids
            ],
        )


async def bound_actor(connection: Connection, channel_id: str) -> str:
    """The actor bound to ``channel_id``; NotAuthorized when it is bound to nobody."""
    actor = await connection.fetchval(
        "SELECT actor_id FROM guilda.channel_identities WHERE channel_id = $1", channel_id
    )
    if actor is None:
        raise NotAuthorized(f"{channel_id} is bound to no actor")
    return str(actor)
