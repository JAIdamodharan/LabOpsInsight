"""The answer renderer (md in app.js) is run in Node: tables must become real tables, and nothing the model writes may become live HTML."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = (Path(__file__).resolve().parent.parent / "analyst" / "static" / "app.js").read_text()
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def render(text: str) -> str:
    esc = re.search(r"^const esc = .*$", JS, re.M).group(0)
    start = JS.index("function md(s) {")
    end = JS.index("\n}\n", start) + 3
    prog = f"{esc}\n{JS[start:end]}\nprocess.stdout.write(md({json.dumps(text)}));"
    return subprocess.run(["node", "-e", prog], capture_output=True, text=True, check=True).stdout


TABLE = """**Key numbers:**

| Instrument | Total tests | OOS % | Avg TAT (days) | On-time % |
|------------|------------|------|----------------|-----------|
| LCMS-02 | 1236 | **1.13** | 2.61 | 92.88 |
| KF-01 | 1476 | 1.29 | 2.45 | 95.93 |

\\*Utilisation = run-hours / (weekday count x 20 h)
"""


def test_a_markdown_table_becomes_a_real_table_with_numeric_columns_marked():
    h = render(TABLE)
    assert h.count("<tr>") == 3 and len(re.findall(r"<th[ >]", h)) == 5 and "<table>" in h and "|" not in h
    assert '<th class="n">Total tests</th>' in h and '<th>Instrument</th>' in h                  # numeric columns align right, header included
    assert '<td class="n">1236</td>' in h and '<td class="n"><b>1.13</b></td>' in h and "<td class=\"\">LCMS-02</td>" in h
    assert "<b>Key numbers:</b>" in h and "&#42;Utilisation" in h                      # an escaped star stays a literal star


def test_alignment_markers_are_honoured():
    h = render("| a | b |\n|:--|--:|\n| x | 1 |")
    assert 'style="text-align:left"' in h and 'style="text-align:right"' in h


def test_lists_headings_and_paragraphs():
    h = render("### Next steps\n\n* first\n* second\n\n1. one\n2. two\n\nplain *italic* and `code`")
    assert "<h4>Next steps</h4>" in h and h.count("<li>") == 4 and "<ul>" in h and "<ol>" in h and "<i>italic</i>" in h and "<code>code</code>" in h


def test_html_and_scripts_from_the_model_are_never_live():
    h = render('<img src=x onerror=alert(1)> <script>alert(1)</script>\n\n| <b>h</b> | b |\n|--|--|\n| <i onclick=1>x</i> | y |')
    assert "<script" not in h and "<img" not in h and "<i onclick" not in h and "&lt;script&gt;" in h


def test_a_pipe_in_ordinary_text_is_not_mistaken_for_a_table():
    h = render("choose a | b when ready\n\nsecond paragraph")
    assert "<table" not in h and h.count("<p>") == 2


def test_blank_input_renders_nothing():
    assert render("") == "" and render("\n\n") == ""
