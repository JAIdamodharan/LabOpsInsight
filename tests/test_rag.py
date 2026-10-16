"""Procedure-document retrieval (BM25), the search_docs tool, the scripted demo, and demo provider auto-detection."""
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from analyst import llm
from analyst.agent import Analyst
from analyst.demo import DemoClient
from analyst.rag import Retriever, tokens
from analyst.server import create_app
from tests.test_analyst import FakeClient, db, msg, text, tool  # noqa: F401
from tests.test_server import events, path  # noqa: F401


@pytest.fixture(scope="module")
def kb():
    return Retriever()


@pytest.mark.parametrize("question,doc", [
    ("what is the specification limit for assay", "SOP-QC-001"),
    ("what must we do when an HPLC shows drift", "SOP-EQ-007"),
    ("how long do we have to close a major deviation", "SOP-QA-003"),
    ("what is the due date for urgent samples", "SOP-QC-020"),
    ("LC-MS breakdown and the backup instrument", "SOP-EQ-011"),
    ("new analysts documentation training refresher", "SOP-HR-005"),
    ("which ALCOA principle do missing analyst ids break", "GUIDE-DI-001"),
])
def test_the_right_document_ranks_in_the_top_two(kb, question, doc):
    assert doc in [h["doc"] for h in kb.search(question, 2)]


def test_unrelated_questions_return_nothing_instead_of_a_wrong_passage(kb):
    assert kb.search("chocolate cake recipe") == []
    assert "do not invent" in kb.as_text("chocolate cake recipe").lower()


def test_passages_carry_a_citable_tag_and_are_capped(kb):
    t = kb.as_text("OOS investigation phase I")
    assert t.startswith("[SOP-QC-012 section ") and all(len(p) < 1100 for p in t.split("\n\n"))


def test_specs_in_the_documents_match_the_data(kb, db):  # noqa: F811
    rows = dict((r[0], (r[1], r[2])) for r in db.run("SELECT method, min(spec_low), max(spec_high) FROM tests GROUP BY 1").rows)
    spec = kb.as_text("specification limits assay related substances dissolution water residual solvents", 3)
    assert rows["Assay"] == (95.0, 105.0) and "95.0" in spec and "105.0" in spec and "5000" in spec and "2.0" in spec


def test_tokens_drop_stop_words_and_fold_plurals():
    assert "the" not in tokens("the limits of the assay") and "limit" in tokens("limits")


def test_search_docs_tool_is_offered_and_executed_for_lab_data_only(db, kb):  # noqa: F811
    lab = Analyst(db, client=FakeClient([msg("tool_use", tool("t1", "search_docs", query="oos investigation")), msg("end_turn", text("done"))]), knowledge=kb)
    assert "search_docs" in [t["name"] for t in lab.tools] and "search_docs" in lab.system
    ev = list(lab.run("q"))
    res = next(e for e in ev if e.kind == "tool_result")
    assert res.data["name"] == "search_docs" and "[SOP-QC-012" in res.data["text"] and not res.data["is_error"]
    other = Analyst(db, client=FakeClient([]), domain="generic", knowledge=kb)
    assert "search_docs" not in [t["name"] for t in other.tools]
    assert "search_docs" not in Analyst(db, client=FakeClient([])).system


def test_scripted_demo_answers_a_procedure_question_with_citations(db, kb):  # noqa: F811
    a = Analyst(db, client=DemoClient(db), knowledge=kb)
    ev = list(a.run("What does the procedure say about investigating an OOS result?"))
    assert [e.data["name"] for e in ev if e.kind == "tool_call"] == ["search_docs"]
    ans = next(e for e in ev if e.kind == "answer").data["text"]
    assert "[SOP-QC-012 section" in ans and "Scope of this walkthrough" in ans


def test_scripted_oos_walkthrough_now_cites_the_procedures(db, kb):  # noqa: F811
    a = Analyst(db, client=DemoClient(db), knowledge=kb)
    ev = list(a.run("Why was the OOS rate on HPLC-03 so high in Mar 2025?"))
    assert "search_docs" in [e.data["name"] for e in ev if e.kind == "tool_call"]
    assert "Procedure references" in next(e for e in ev if e.kind == "answer").data["text"]


def test_a_demo_with_a_real_client_answers_with_it_over_the_lab_data(path, tmp_path):  # noqa: F811
    fake = FakeClient([msg("tool_use", tool("t1", "search_docs", query="calibration drift")), msg("end_turn", text("Per [SOP-EQ-007 section 2] stop using the instrument."))])
    c = TestClient(create_app(path, demo=False, client=fake, model="m", artifacts_dir=str(tmp_path / "a"), upload_dir=str(tmp_path / "up")))
    ev = events(c.get("/api/ask", params={"q": "what should we do about a drifting HPLC"}))
    assert [e["kind"] for e in ev] == ["tool_call", "tool_result", "answer", "done"] and "SOP-EQ-007" in ev[-2]["text"]
    assert c.get("/api/config").json()["demo"] is False


def test_detect_provider_prefers_an_explicit_choice_then_a_real_key_and_ignores_placeholders(monkeypatch):
    for k in ("ANALYST_PROVIDER", "GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    assert llm.detect_provider() is None
    monkeypatch.setenv("GROQ_API_KEY", "paste-your-key-here")
    assert llm.detect_provider() is None                                           # a placeholder is not a key
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "a1B2c3D4e5F6" * 4)
    assert llm.detect_provider() == "groq"
    monkeypatch.setenv("ANALYST_PROVIDER", "ollama")
    assert llm.detect_provider() == "ollama"


def test_the_last_allowed_step_forces_a_written_answer_instead_of_stopping_silently(db):  # noqa: F811
    seen = []

    class Looper:
        def __init__(self):
            self.messages = NS(create=self._c); self.n = 0

        def _c(self, **kw):
            self.n += 1
            seen.append(("tools" in kw, kw["messages"][-1]["content"] if isinstance(kw["messages"][-1]["content"], str) else "results"))
            if "tools" in kw:
                return msg("tool_use", tool(f"t{self.n}", "run_sql", sql="SELECT 1"))
            return msg("end_turn", text("Best answer from what I found."))
    a = Analyst(db, client=Looper(), max_steps=3)
    ev = list(a.run("q"))
    assert a.last_stats.stop_reason == "answered" and a.last_stats.steps == 3
    assert [s[0] for s in seen] == [True, True, False] and "Do not call any more tools" in seen[-1][1]
    assert next(e for e in ev if e.kind == "answer").data["text"].startswith("Best answer")
