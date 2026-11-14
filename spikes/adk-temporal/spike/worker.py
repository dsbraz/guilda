"""Worker em processo separado: pode morrer sem derrubar Temporal ou o portal."""

import argparse
import asyncio
import json
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from spike.activities import Activities
from spike.agent import Proposer
from spike.fixture_model import FixtureModel
from spike.workflow import AllocationWorkflow


async def run(args):
    config = json.loads(Path(args.config).read_text()) if args.config else None
    model = args.model
    if model == "fixture":
        model = FixtureModel(capacity_tool=bool(config and "get_capacity" in config["tools"]))
    activities = Activities(Proposer(model, config), args.portal, args.journal)
    client = await Client.connect(args.address)
    worker = Worker(
        client,
        task_queue=args.queue,
        workflows=[AllocationWorkflow],
        activities=activities.registered(),
    )
    await worker.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    parser.add_argument("--queue", required=True)
    parser.add_argument("--portal", required=True)
    parser.add_argument("--journal", required=True)
    parser.add_argument("--model", default="fixture")
    parser.add_argument("--config")
    asyncio.run(run(parser.parse_args()))
