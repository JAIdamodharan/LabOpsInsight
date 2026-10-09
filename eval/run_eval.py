"""Run the agent over the question set and write a results JSON + markdown report.

  python -m eval.run_eval --price-in 3 --price-out 15      # $/Mtok, to report cost
  python -m eval.run_eval --ids q01,q17 --model claude-haiku-5-5
"""
import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path

from analyst.agent import Analyst
from analyst.db import Database
from analyst.llm import add_cli_args, make_client

from .grading import grade


def run_one(agent: Analyst, q: dict, db: Database) -> dict:
    t0 = time.perf_counter()
    try:
        for _ in agent.run(q["question"]):
            pass
        s = agent.last_stats
        passed, reason = grade(q, s.answer, db) if s.stop_reason == "answered" else (False, f"budget:{s.stop_reason}")
        row = dict(steps=s.steps, in_tok=s.input_tokens, out_tok=s.output_tokens,
                   sql_calls=s.sql_calls, sql_errors=s.sql_errors, stop=s.stop_reason, answer=s.answer)
    except Exception as e:  # API/network errors count as failures, not crashes
        passed, reason = False, f"exception:{type(e).__name__}"
        row = dict(steps=0, in_tok=0, out_tok=0, sql_calls=0, sql_errors=0, stop="exception", answer=str(e))
    return dict(id=q["id"], difficulty=q["difficulty"], question=q["question"], passed=passed,
                reason=reason, latency_s=round(time.perf_counter() - t0, 1), **row)


def report(rows: list[dict], model: str, price_in: float, price_out: float) -> str:
    n = len(rows)
    acc = sum(r["passed"] for r in rows) / n
    cost = lambda r: (r["in_tok"] * price_in + r["out_tok"] * price_out) / 1e6
    had_err = [r for r in rows if r["sql_errors"] > 0]
    recovered = [r for r in had_err if r["passed"]]
    L = [f"# Eval report — {model}", "",
         f"- **Accuracy:** {acc:.0%} ({sum(r['passed'] for r in rows)}/{n})",
         f"- **Median latency:** {statistics.median(r['latency_s'] for r in rows):.1f}s",
         f"- **Avg steps:** {statistics.mean(r['steps'] for r in rows):.1f}",
         f"- **Avg tokens/question:** {statistics.mean(r['in_tok'] + r['out_tok'] for r in rows):,.0f}"]
    if price_in or price_out:
        L.append(f"- **Avg cost/question:** ${statistics.mean(cost(r) for r in rows):.4f}"
                 f" · **cost per correct answer:** ${sum(cost(r) for r in rows) / max(1, sum(r['passed'] for r in rows)):.4f}")
    if had_err:
        L.append(f"- **Self-correction:** {len(recovered)}/{len(had_err)} questions with SQL errors still answered correctly")
    L += ["", "| difficulty | passed | total |", "|---|---|---|"]
    for d in ["easy", "medium", "hard", "trap"]:
        sub = [r for r in rows if r["difficulty"] == d]
        if sub:
            L.append(f"| {d} | {sum(r['passed'] for r in sub)} | {len(sub)} |")
    fails = Counter(r["reason"].split(" ")[0].split(":")[0] for r in rows if not r["passed"])
    if fails:
        L += ["", "## Failure taxonomy", *[f"- `{k}`: {v}" for k, v in fails.most_common()]]
        L += ["", "## Failed questions", *[f"- **{r['id']}** {r['question']} → {r['reason']}" for r in rows if not r["passed"]]]
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/shop.duckdb")
    ap.add_argument("--questions", default="eval/questions.json")
    add_cli_args(ap)
    ap.add_argument("--ids", help="comma-separated question ids")
    ap.add_argument("--price-in", type=float, default=0.0, help="USD per million input tokens")
    ap.add_argument("--price-out", type=float, default=0.0, help="USD per million output tokens")
    ap.add_argument("--out", default="eval/results")
    args = ap.parse_args()

    qs = json.loads(Path(args.questions).read_text())
    if args.ids:
        qs = [q for q in qs if q["id"] in args.ids.split(",")]
    db = Database(args.db)
    client, model = make_client(args.provider, args.model, args.base_url)
    agent = Analyst(db, client=client, model=model)

    rows = []
    for q in qs:
        r = run_one(agent, q, db)
        rows.append(r)
        print(f"{'PASS' if r['passed'] else 'FAIL'} {r['id']} {r['latency_s']:>5}s {r['steps']} steps  {r['reason']}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out / f"{stamp}.json").write_text(json.dumps(rows, indent=2, default=str))
    md = report(rows, f"{args.provider}/{model}", args.price_in, args.price_out)
    (out / f"{stamp}.md").write_text(md)
    print("\n" + md)


if __name__ == "__main__":
    main()
