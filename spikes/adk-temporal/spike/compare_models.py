"""Comparação pequena e pareada com modelos reais; preserva cada tentativa em JSON."""

import argparse
import asyncio
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from statistics import median

from spike.agent import ROOT, Proposer
from spike.fixture_model import AMBIGUOUS, COMPLETE, MISSING_DATE, MOVE

MODELS = ["gemini-3.8-flash", "gemini-4-hydrogen"]
CASES = {"complete": COMPLETE, "missing_date": MISSING_DATE, "ambiguous": AMBIGUOUS}


def passes(case, result):
    if case == "complete":
        return result["status"] == "proposed" and result["move"] == MOVE
    return (
        result["status"] == "needs_clarification"
        and result["move"] is None
        and bool(result["question"])
    )


def summarize(rows):
    summary = {}
    for model in MODELS:
        selected = [row for row in rows if row["model"] == model]
        if not selected:
            continue
        summary[model] = {
            "attempts": len(selected),
            "passed": sum(row["passed"] for row in selected),
            "median_seconds_all_attempts": median(row["seconds"] for row in selected),
            "tokens": {
                key: sum(
                    usage.get(key, 0)
                    for row in selected
                    for usage in row["metrics"].get("usage", [])
                )
                for key in (
                    "prompt_token_count",
                    "candidates_token_count",
                    "thoughts_token_count",
                    "total_token_count",
                )
            },
        }
    return summary


async def main(args):
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "versions": {name: version(name) for name in ("google-adk", "google-genai")},
        "temperature": 0,
        "thinking": "provider default for each model",
        "repetitions": args.repetitions,
        "timeout_seconds": args.timeout,
        "fixtures_sha256": {
            name: hashlib.sha256((ROOT / "fixtures" / name).read_bytes()).hexdigest()
            for name in ("agent.json", "context.json")
        },
        "cases": CASES,
        "runs": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for repeat in range(args.repetitions):
        for index, (case, request) in enumerate(CASES.items()):
            order = MODELS if (repeat + index) % 2 == 0 else MODELS[::-1]
            for model in order:
                row = {"model": model, "case": case, "repeat": repeat + 1, "metrics": {}}
                started = time.perf_counter()
                try:
                    result = await asyncio.wait_for(
                        Proposer(model).propose(request, metrics=row["metrics"]), args.timeout
                    )
                    row.update(result=result, passed=passes(case, result))
                except Exception as exc:
                    # Não persistir mensagens de transporte que possam conter credenciais.
                    row.update(passed=False, error_type=type(exc).__name__)
                row["seconds"] = round(time.perf_counter() - started, 3)
                report["runs"].append(row)
                report["summary"] = summarize(report["runs"])
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(model, case, repeat + 1, row["passed"], row["seconds"], flush=True)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/hydrogen-comparison.json")
    args = parser.parse_args()
    if args.repetitions < 1 or args.timeout <= 0:
        parser.error("repetitions e timeout precisam ser positivos")
    asyncio.run(main(args))
