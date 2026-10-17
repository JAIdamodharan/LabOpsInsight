"""The Vercel bundle must contain every file the app reads at run time (a stray ignore rule once removed the procedure documents)."""
import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ignored(rel: str) -> bool:
    rules = [l.strip() for l in (ROOT / ".vercelignore").read_text().splitlines() if l.strip() and not l.startswith("#")]
    hit = False
    for r in rules:
        neg = r.startswith("!")
        pat = r[1:] if neg else r
        match = (rel.startswith(pat) if pat.endswith("/") else fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat) or rel == pat)
        if match:
            hit = not neg
    return hit


def test_runtime_files_are_not_excluded_from_the_bundle():
    needed = [p.relative_to(ROOT).as_posix() for p in [*ROOT.glob("analyst/*.py"), *ROOT.glob("analyst/knowledge/*_*.md"), ROOT / "analyst/story.html",
              *ROOT.glob("analyst/static/*.*"), *ROOT.glob("analyst/static/photos/hero-*.jpg"), ROOT / "scripts/make_data.py", ROOT / "api/index.py",
              ROOT / "requirements.txt", ROOT / "vercel.json"] if p.exists()]
    assert len(needed) > 20
    assert [n for n in needed if ignored(n)] == []


def test_junk_and_secrets_are_excluded():
    for rel in (".env", ".env.local", "tests/test_rag.py", "data/labops.duckdb", ".venv/lib/x.py", "sample_data/a.xlsx"):
        assert ignored(rel), rel


def test_vercel_json_routes_every_path_to_the_function_keeping_the_original_path():
    import json
    cfg = json.loads((ROOT / "vercel.json").read_text())
    assert cfg["routes"] == [{"src": "/(.*)", "dest": "/api/index.py"}] and "rewrites" not in cfg
