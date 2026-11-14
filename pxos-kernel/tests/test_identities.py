"""Identity configuration (PXOS-51, PXOS-61): people, agents and external actors."""

from pathlib import Path

import pytest

from pxos_kernel.identity import InvalidIdentities, read_identities


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
