"""Query-focused markdown: keep only the passages relevant to a query.

When read_markdown is given query=…, an agent doing research doesn't want the whole page
back — it wants the passages that answer the query. This is crawl4ai's BM25ContentFilter,
hand-rolled with no dependency (rank_bm25/snowballstemmer aren't installed here):

  - split the cleaned markdown into structural chunks (headings start new chunks; blank
    lines separate paragraphs; lists/tables/code blocks stay atomic),
  - score each chunk with BM25-Okapi against the query tokens,
  - weight headings/strong/code higher (a matching H2 is worth more than a matching <td>),
  - drop chunks below threshold, return the survivors in original document order.

Returns markdown (the relevant passages) so the existing pipeline is unchanged.
"""
import math
import re

_WORD = re.compile(r"[A-Za-z0-9]+")


def _tok(s):
    return _WORD.findall((s or "").lower())


def _split_chunks(md):
    """Split markdown into atomic chunks. Headings, list groups, tables, and fenced code
    stay whole; blank lines separate paragraphs."""
    lines = md.split("\n")
    chunks, cur, in_fence = [], [], False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            cur.append(ln)
            continue
        if in_fence:
            cur.append(ln)
            continue
        if ln.startswith("#"):                     # heading -> its own chunk boundary
            if cur:
                chunks.append("\n".join(cur).strip())
            cur = [ln]
            continue
        if not ln.strip():                          # blank line -> paragraph break
            if cur:
                chunks.append("\n".join(cur).strip())
                cur = []
            continue
        cur.append(ln)
    if cur:
        chunks.append("\n".join(cur).strip())
    return [c for c in chunks if c]


def _weight(chunk):
    """Priority weight: headings/bold/code carry more signal than body text."""
    if chunk.startswith("#"):
        h = len(chunk) - len(chunk.lstrip("#"))
        return {1: 3.0, 2: 2.5, 3: 2.0}.get(h, 1.8)
    if chunk.startswith(("```", "    ")):
        return 1.6
    if chunk.startswith(("- ", "* ", "1.")):
        return 1.2
    return 1.0


def bm25_filter(md, query, top_k=None, threshold=0.0, k1=1.5, b=0.75, min_chunks=3):
    """Return the query-relevant subset of `md` as markdown, in document order.
    Falls back to the full markdown when query is empty or nothing scores."""
    q = _tok(query)
    if not md or not q:
        return md
    chunks = _split_chunks(md)
    if len(chunks) <= min_chunks:
        return md
    toks = [_tok(c) for c in chunks]
    N = len(chunks)
    avgdl = sum(len(t) for t in toks) / N or 1.0
    # document frequency per query term
    df = {}
    for term in set(q):
        df[term] = sum(1 for t in toks if term in t)
    scores = []
    for i, t in enumerate(toks):
        dl = len(t) or 1
        s = 0.0
        for term in q:
            n = df.get(term, 0)
            if n == 0:
                continue
            idf = math.log(1 + (N - n + 0.5) / (n + 0.5))
            f = t.count(term)
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s * _weight(chunks[i]))
    ranked = sorted(range(N), key=lambda i: scores[i], reverse=True)
    keep = [i for i in ranked if scores[i] > threshold]
    if not keep:
        return md
    if top_k:
        keep = keep[:top_k]
    keep_set = set(keep)
    return "\n\n".join(chunks[i] for i in range(N) if i in keep_set).strip()
