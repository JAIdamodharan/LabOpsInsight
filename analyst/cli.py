"""Usage: python -m analyst.cli "Why did revenue drop in March 2025?" [--db data/shop.duckdb]"""
import argparse
import sys

from .agent import Analyst
from .db import Database


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--db", default="data/shop.duckdb")
    args = ap.parse_args()

    agent = Analyst(Database(args.db))
    for ev in agent.run(args.question):
        if ev.kind == "tool_call":
            print(f"\n\033[36m> {ev.data['name']}\033[0m {ev.data['input'].get('sql', '')}")
        elif ev.kind == "tool_result":
            tag = "\033[31m" if ev.data["is_error"] else "\033[90m"
            print(f"{tag}{ev.data['text'][:800]}\033[0m")
        elif ev.kind == "answer":
            print(f"\n\033[1m{ev.data['text']}\033[0m")
        elif ev.kind == "stopped":
            print(f"\nStopped: {ev.data['reason']}", file=sys.stderr)
    s = agent.last_stats
    print(f"\n[{s.steps} steps, {s.sql_calls} queries ({s.sql_errors} errors), "
          f"{s.input_tokens} in / {s.output_tokens} out tokens]")


if __name__ == "__main__":
    main()
