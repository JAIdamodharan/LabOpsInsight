"""Vercel entrypoint: a public, read-only demo of LabOps Insight.

Vercel functions have a read-only disk (only /tmp is writable) and no long-lived state, so this entrypoint:
  * builds the synthetic lab database into /tmp on a cold start (about a second),
  * runs the app in public mode (no accounts, no uploads, no code sandbox, limits per visitor address),
  * answers with the scripted walkthrough unless LABOPS_DEMO_LIVE=1 and a provider key (e.g. GROQ_API_KEY) is set in the project settings.
"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(os.environ.get("LABOPS_TMP", "/tmp")) / "labops"
TMP.mkdir(parents=True, exist_ok=True)
DB = TMP / "labops.duckdb"

if not DB.exists():
    spec = importlib.util.spec_from_file_location("make_data", ROOT / "scripts" / "make_data.py")
    make_data = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_data)
    make_data.build(DB)

from analyst.llm import detect_provider, make_client  # noqa: E402
from analyst.server import create_app  # noqa: E402

client = model = None
if os.environ.get("LABOPS_DEMO_LIVE") == "1":
    provider = detect_provider()
    if provider:
        try:
            client, model = make_client(provider)
        except SystemExit as e:                                       # a bad key must not take the whole site down
            print(f"live demo disabled: {e}")

app = create_app(str(DB), demo=client is None, sandbox="none", artifacts_dir=str(TMP / "artifacts"), upload_dir=str(TMP / "uploads"),
                 client=client, model=model, public=True)
