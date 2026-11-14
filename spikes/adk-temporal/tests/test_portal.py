import httpx
import pytest

from spike.activities import Activities
from spike.fixture_model import MOVE
from spike.local import wait_portal

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.mark.parametrize("idempotent,effects", [(True, 1), (False, 2)])
async def test_portal_really_duplicates_without_idempotency(portal, idempotent, effects):
    await wait_portal(portal)
    body = {"operation_id": "probe", "payload": MOVE, "idempotent": idempotent}
    async with httpx.AsyncClient() as client:
        for _ in range(2):
            assert (await client.post(portal + "/moves", json=body)).status_code == 200
        stats = (await client.get(portal + "/stats/probe")).json()
        assert stats == {"attempts": 2, "effects": effects}
        if idempotent:
            body["payload"] = {**MOVE, "percentage": 80}
            assert (await client.post(portal + "/moves", json=body)).status_code == 409


async def test_executor_refuses_changed_approval(portal, tmp_path):
    from temporalio.exceptions import ApplicationError

    activities = Activities(None, portal, str(tmp_path / "context.db"))
    with pytest.raises(ApplicationError, match="digest divergente"):
        await activities.execute({"envelope": {"payload": MOVE}, "approved_digest": "stale"})
