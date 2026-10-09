"""Usage: python -m analyst.cli "Why did revenue drop in March 2025?" [--db data/shop.duckdb]"""
import argparse
import sys

from .agent import Analyst
from .db import Database
from .sandbox import DockerSandbox, LocalSandbox


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--db", default="data/shop.duckdb")
    ap.add_argument("--sandbox", choices=["docker", "local", "none"], default="none",
                    help="enable run_python (docker = isolated; local = DEV ONLY, not isolated)")
    args = ap.parse_args()

    sandbox = {"docker": DockerSandbox, "local": LocalSandbox, "none": None}[args.sandbox]
    agent = Analyst(Database(args.db), sandbox=sandbox(args.db) if sandbox else None)
    for ev in agent.run(args.question):
        if ev.kind == "tool_call":
            print(f"\n\033[36m> {ev.data['name']}\033[0m {ev.data['input'].get('sql') or ev.data['input'].get('code', '')}")
        elif ev.kind == "tool_result":
            tag = "\033[31m" if ev.data["is_error"] else "\033[90m"
            print(f"{tag}{ev.data['text'][:800]}\033[0m")
        elif ev.kind == "answer":
            print(f"\n\033[1m{ev.data['text']}\033[0m")
        elif ev.kind == "stopped":
            print(f"\nStopped: {ev.data['reason']}", file=sys.stderr)
    s = agent.last_stats
    if s.artifacts:
        print("Charts/files:", *s.artifacts)
    print(f"\n[{s.steps} steps, {s.sql_calls} queries ({s.sql_errors} errors), "
          f"{s.input_tokens} in / {s.output_tokens} out tokens]")


if __name__ == "__main__":
    main()
