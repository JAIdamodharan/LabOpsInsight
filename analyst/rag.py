"""Small local retrieval over the lab's synthetic procedure documents (BM25, no embeddings, no network).

Documents live in analyst/knowledge/*.md. Each is split into sections at '## ' headings; a section is the unit that is retrieved and cited
as `[SOP-QC-012 section 2]`.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KNOWLEDGE = Path(__file__).parent / "knowledge"
STOP = set("a an and are as at be by for from how in is it of on or that the this to was what when which who why with".split())
K1, B = 1.4, 0.75


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9\-\.]*", text.lower()):
        w = w.rstrip(".-")
        if not w or w in STOP:
            continue
        out.append(w)
        if len(w) > 4 and w.endswith("s"):          # crude plural folding is enough for these documents
            out.append(w[:-1])
    return out


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    title: str
    section: str
    text: str


def load_chunks(folder: Path = KNOWLEDGE) -> list[Chunk]:
    chunks = []
    for f in sorted(folder.glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        doc_id = f.name.split("_")[0]
        title = next((l[2:].strip() for l in raw.splitlines() if l.startswith("# ")), doc_id)
        for part in re.split(r"\n(?=## )", raw):
            head = part.splitlines()[0] if part.startswith("## ") else "Overview"
            body = part.split("\n", 1)[1] if part.startswith("## ") else part
            body = body.strip()
            if len(body) > 120:                             # skip a bare title/disclaimer block
                num = re.match(r"##\s*(\d+)", head)
                chunks.append(Chunk(doc_id, title, num.group(1) if num else head.lstrip("# ").strip(), f"{head.lstrip('# ').strip()}\n{body}" if part.startswith("## ") else body))
    return chunks


class Retriever:
    def __init__(self, chunks: list[Chunk] | None = None):
        self.chunks = chunks if chunks is not None else load_chunks()
        self.tf = [Counter(tokens(f"{c.title} {c.text}")) for c in self.chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df = Counter(w for t in self.tf for w in t)
        n = len(self.chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query: str, k: int = 3) -> list[dict]:
        q = tokens(query)
        scored = []
        for i, tf in enumerate(self.tf):
            s = sum(self.idf.get(w, 0) * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * self.len[i] / self.avg)) for w in set(q) if w in tf)
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        return [{"doc": self.chunks[i].doc_id, "title": self.chunks[i].title, "section": self.chunks[i].section, "text": self.chunks[i].text, "score": round(s, 2)} for s, i in scored[:k]]

    def as_text(self, query: str, k: int = 3) -> str:
        hits = self.search(query, k)
        if not hits:
            return "No matching passage in the procedure documents. Do not invent a policy; say the documents do not cover it."
        return "\n\n".join(f"[{h['doc']} section {h['section']}] {h['title']}\n{h['text'][:900]}" for h in hits)
