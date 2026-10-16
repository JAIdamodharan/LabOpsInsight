"""Every generated sample workbook must upload cleanly (this is what the files are for), and generation must be deterministic."""
import hashlib
import importlib.util
from pathlib import Path

import pytest

from analyst.ingest import ingest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_datasets", ROOT / "scripts" / "make_datasets.py")
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    md.OUT = tmp_path_factory.mktemp("sample_data")
    md.main([])
    return md.OUT


@pytest.mark.parametrize("name", list(md.FILES))
def test_each_sample_file_ingests_without_error(made, tmp_path, name):
    r = ingest(name, (made / name).read_bytes(), tmp_path / "x.duckdb")
    assert r.tables and all(t.rows > 0 for t in r.tables)


def test_the_messy_file_really_triggers_the_data_review_warnings(made, tmp_path):
    r = ingest("messy_orders.xlsx", (made / "messy_orders.xlsx").read_bytes(), tmp_path / "m.duckdb")
    assert r.warnings                                                                  # numbers-as-text and blanks are reported, never silently fixed


def test_generation_is_deterministic(tmp_path):
    def digest():
        return hashlib.sha256(md.hr_attrition()["employees"].to_csv().encode()).hexdigest()
    md.RNG = __import__("numpy").random.default_rng(2025)
    a = digest()
    md.RNG = __import__("numpy").random.default_rng(2025)
    assert a == digest()
