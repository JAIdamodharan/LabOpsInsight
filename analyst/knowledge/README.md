# Procedure documents

Eight **synthetic** lab procedures used by the agent's `search_docs` tool (retrieval in `analyst/rag.py`). They are teaching material written for this project; they are not any real company's SOPs.

Add a document by dropping a Markdown file here. Name it `ID_title.md` (the part before the first underscore is the citation id), start with a `# Title` line, and split it into `## 1. Section` headings. Sections shorter than about 120 characters are ignored. `tests/test_rag.py` checks retrieval, and that the specification table in `SOP-QC-001` matches the data.
