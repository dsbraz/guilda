"""Identity configuration (PXOS-51, PXOS-61): people, agents and external actors.

An actor may be bound to identities on a channel; a channel identity names one actor, and
one bound to nobody (a guest, a stranger) names none.
"""

from pathlib import Path

import asyncpg
import pytest

from guilda_kernel.identity import InvalidIdentities, NotAuthorized, bound_actor, read_identities


def test_an_agent_cannot_act_for_an_external_actor(tmp_path: Path) -> None:
    path = tmp_path / "identities.yaml"
    path.write_text(
        """
actors:
  - {id: vera, kind: external_person, name: Vera}
  - {id: helper, kind: agent, name: Helper}
delegations:
  - {agent: helper, person: vera}
""",
        encoding="utf-8",
    )
    with pytest.raises(InvalidIdentities, match="vera is not a person"):
        read_identities(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("actors: [{id: ana, kind: robot, name: Ana}]", "identities.yaml"),
        (
            "actors: [{id: ana, kind: internal_person, name: Ana},"
            " {id: ana, kind: agent, name: Outra}]",
            "duplicated actor ids",
        ),
        (
            "actors: [{id: ana, kind: internal_person, name: Ana},"
            " {id: bia, kind: internal_person, name: Bia}]\n"
            "delegations: [{agent: bia, person: ana}]",
            "bia is not an agent",
        ),
    ],
    ids=["unknown-kind", "duplicated-id", "person-as-agent"],
)
def test_an_invalid_identity_file_is_refused(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "identities.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(InvalidIdentities, match=message):
        read_identities(path)


async def test_a_channel_identity_names_the_actor_bound_to_it(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as connection:
        assert await bound_actor(connection, "chat:erin") == "erin"
        assert await bound_actor(connection, "chat:vera") == "vera"
        with pytest.raises(NotAuthorized, match="chat:guest"):
            await bound_actor(connection, "chat:guest")
