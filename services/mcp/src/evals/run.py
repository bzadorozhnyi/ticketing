"""Runs the evals: every scenario `--trials` times on a running stack, then a table.
Usage, from services/mcp/src:
    uv run python -m evals.run [--scenario NAME ...] [--trials 3]
"""

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from evals.claude_code import ClaudeCodeRunner
from evals.scenarios import SCENARIOS, Scenario
from evals.stack import Stack


def run_scenario(
    scenario: Scenario, *, trials: int, stack: Stack, runner: ClaudeCodeRunner
) -> list[dict]:
    results = []
    for trial in range(1, trials + 1):
        tag = uuid.uuid4().hex[:6]
        case = scenario.prepare(stack, tag)
        run = runner.run(prompt=case.prompt, user_id=case.user_id, stack=stack)
        failures = (
            [f"run failed: {run.failed}"]
            if run.failed
            else scenario.grade(stack, case, run)
        )
        tools = ", ".join(
            call.name + ("!" if call.is_error else "") for call in run.calls
        )
        verdict = "pass" if not failures else "FAIL " + "; ".join(failures)
        print(
            f"  {scenario.name} #{trial}: {verdict}  [{run.turns} turns, "
            f"{run.seconds:.0f}s, ${run.cost_usd:.3f}] {tools}",
            flush=True,
        )
        results.append(
            {
                "scenario": scenario.name,
                "trial": trial,
                "tag": tag,
                "passed": not failures,
                "failures": failures,
                "prompt": case.prompt,
                "answer": run.answer,
                "calls": [call.model_dump() for call in run.calls],
                "turns": run.turns,
                "seconds": run.seconds,
                "cost_usd": run.cost_usd,
            }
        )
    return results


def summary(results: list[dict]) -> None:
    print(
        f"\n{'scenario':28} {'passed':>7} {'pass@k':>7} {'pass^k':>7} "
        f"{'turns':>6} {'seconds':>8} {'$':>7}"
    )
    for scenario in SCENARIOS:
        mine = [r for r in results if r["scenario"] == scenario.name]
        if not mine:
            continue
        passed = sum(r["passed"] for r in mine)
        name = scenario.name + (" (safety)" if scenario.safety else "")
        print(
            f"{name:28} {passed:>4}/{len(mine):<2} {('yes' if passed else 'no'):>7} "
            f"{('yes' if passed == len(mine) else 'no'):>7} "
            f"{sum(r['turns'] for r in mine) / len(mine):>6.1f} "
            f"{sum(r['seconds'] for r in mine) / len(mine):>8.0f} "
            f"{sum(r['cost_usd'] for r in mine):>7.2f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", action="append", help="run only these (repeatable)"
    )
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument(
        "--stack",
        default=os.environ.get("EVALS_STACK", "kind"),
        choices=["kind", "compose"],
    )
    parser.add_argument("--kubeconfig", default=os.environ.get("KUBECONFIG"))
    args = parser.parse_args()
    stack = Stack(kind=args.stack, kubeconfig=args.kubeconfig)
    runner = ClaudeCodeRunner(model=args.model)
    chosen = [s for s in SCENARIOS if not args.scenario or s.name in args.scenario]
    results = []
    for scenario in chosen:
        results += run_scenario(
            scenario, trials=args.trials, stack=stack, runner=runner
        )
    summary(results)
    out = Path(tempfile.mkdtemp(prefix="evals-results-")) / "results.json"
    out.write_text(json.dumps(results, indent=1))
    print(f"\nEvery run, with the agent's answer and tool calls: {out}")


if __name__ == "__main__":
    main()
