import os
import socket
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio.testing import WorkflowEnvironment

ROOT = Path(__file__).resolve().parents[1]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def temporal():
    directory = ROOT / ".runtime"
    directory.mkdir(exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        download_dest_dir=str(directory),
        dev_server_download_version="v1.10.1",
        dev_server_startup_timeout=timedelta(seconds=30),
    ) as env:
        yield env


@pytest.fixture
def portal(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "portal.log").open("w")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "spike.portal:app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env={**os.environ, "SPIKE_PORTAL_DB": str(tmp_path / "portal.db")},
        stdout=log,
        stderr=log,
    )
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()
