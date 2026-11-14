"""Utilitários locais para demonstração e testes em processos separados."""

import asyncio
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


async def wait_portal(url):
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            try:
                if (await client.get(url + "/health")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError("portal não iniciou")


class WorkerProcess:
    def __init__(self, address, queue, portal, directory, model="fixture", config=None):
        self.command = [
            sys.executable,
            "-m",
            "spike.worker",
            "--address",
            address,
            "--queue",
            queue,
            "--portal",
            portal,
            "--journal",
            str(directory / "context.db"),
            "--model",
            model,
        ]
        if config:
            self.command += ["--config", str(config)]
        self.directory = directory
        self.process = None
        self.log = None

    def start(self):
        self.log = (self.directory / "worker.log").open("a")
        self.process = subprocess.Popen(self.command, cwd=ROOT, stdout=self.log, stderr=self.log)

    def kill(self):
        if self.process and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        if self.log:
            self.log.close()

    def assert_alive(self):
        if self.process.poll() is not None:
            raise AssertionError((self.directory / "worker.log").read_text())
