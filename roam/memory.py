import json
import os
import re
import sqlite3
import time
from urllib.parse import urlparse


def _key(url):
    u = urlparse(url)
    return u.netloc, (u.path or "/")


def _tokens(s):
    return re.findall(r"\w+", (s or "").lower())


def _tok_match(a, b):
    # exact, or a stem/prefix relationship (search~searching, product~products)
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def rank_score(query_tokens, text):
    """Fraction of query tokens that match (stem/prefix-aware) some token in `text`.
    Dependency-free improved-lexical ranking — not true embeddings, but handles plural/
    stem variants that exact substring matching misses."""
    ttok = _tokens(text)
    if not query_tokens or not ttok:
        return 0.0
    hits = sum(1 for q in query_tokens if any(_tok_match(q, t) for t in ttok))
    return hits / len(query_tokens)


# Computes a durable CSS selector + role + accessible name for an element.
# Runs as a Playwright Locator.evaluate (the element is the implicit arg).
REMEMBER_JS = r"""
(el) => {
  if (!el) return null;
  const durable = (() => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName !== 'BODY' && parts.length < 5) {
      let sel = node.tagName.toLowerCase();
      const tid = node.getAttribute('data-testid');
      if (tid) { parts.unshift(sel + '[data-testid="' + tid + '"]'); break; }
      const parent = node.parentElement;
      if (parent) {
        const sib = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sib.length > 1) sel += ':nth-of-type(' + (sib.indexOf(node) + 1) + ')';
      }
      parts.unshift(sel);
      node = node.parentElement;
    }
    return parts.join(' > ');
  })();
  const role = el.getAttribute('role') || ({A:'link',BUTTON:'button',SELECT:'combobox',TEXTAREA:'textbox',INPUT:(el.type==='submit'||el.type==='button')?'button':'textbox'}[el.tagName] || el.tagName.toLowerCase());
  const name = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
    el.getAttribute('alt') || (el.innerText || el.textContent || '').trim())
    .replace(/\s+/g, ' ').slice(0, 120);
  return { selector: durable, role, name };
}
"""


class SelectorMemory:
    """Local, growing store of verified selectors keyed by site. No values are stored."""

    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS selectors(
                    domain TEXT, path TEXT, role TEXT, name TEXT, selector TEXT,
                    hits INTEGER DEFAULT 1, last_ts INTEGER, fingerprint TEXT,
                    PRIMARY KEY(domain, path, role, name))"""
            )
            try:  # migrate older dbs
                c.execute("ALTER TABLE selectors ADD COLUMN fingerprint TEXT")
            except sqlite3.OperationalError:
                pass
            # action manuals: a named, ordered sequence of steps per site (the moat).
            c.execute(
                """CREATE TABLE IF NOT EXISTS manuals(
                    domain TEXT, name TEXT, steps TEXT,
                    hits INTEGER DEFAULT 1, last_ts INTEGER,
                    PRIMARY KEY(domain, name))"""
            )
            # API recipes: the internal API calls a site makes, captured from real browsing
            # (actionbook's real moat). Claude turns these into direct-fetch shortcuts.
            c.execute(
                """CREATE TABLE IF NOT EXISTS recipes(
                    domain TEXT, name TEXT, method TEXT, api_url TEXT, resp_keys TEXT,
                    hits INTEGER DEFAULT 1, last_ts INTEGER,
                    PRIMARY KEY(domain, name))"""
            )

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def record(self, url, role, name, selector, fingerprint=None, ts=None):
        if not selector:
            return
        domain, path = _key(url)
        ts = ts if ts is not None else int(time.time())
        fp = json.dumps(fingerprint) if fingerprint else None
        with self._conn() as c:
            c.execute(
                """INSERT INTO selectors(domain, path, role, name, selector, hits, last_ts, fingerprint)
                   VALUES(?,?,?,?,?,1,?,?)
                   ON CONFLICT(domain, path, role, name) DO UPDATE SET
                     hits = hits + 1, selector = excluded.selector, last_ts = excluded.last_ts,
                     fingerprint = COALESCE(excluded.fingerprint, selectors.fingerprint)""",
                (domain, path, role, name, selector, ts, fp),
            )

    def fingerprint_for(self, url=None, domain=None, role=None, name=None):
        if url and "://" in url:
            domain = _key(url)[0]
        with self._conn() as c:
            r = c.execute(
                "SELECT fingerprint FROM selectors WHERE domain=? AND role=? AND name=? "
                "AND fingerprint IS NOT NULL ORDER BY hits DESC LIMIT 1",
                (domain, role, name)).fetchone()
        return json.loads(r["fingerprint"]) if r and r["fingerprint"] else None

    def update_selector(self, url=None, domain=None, role=None, name=None, selector=None):
        if url and "://" in url:
            domain = _key(url)[0]
        with self._conn() as c:
            return c.execute(
                "UPDATE selectors SET selector=? WHERE domain=? AND role=? AND name=?",
                (selector, domain, role, name)).rowcount

    def recall(self, url=None, domain=None, query=None):
        path = None
        if url:
            domain, path = _key(url)
        with self._conn() as c:
            if domain and path:
                rows = c.execute(
                    "SELECT * FROM selectors WHERE domain=? AND path=? ORDER BY hits DESC",
                    (domain, path)).fetchall()
            elif domain:
                rows = c.execute(
                    "SELECT * FROM selectors WHERE domain=? ORDER BY hits DESC",
                    (domain,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM selectors ORDER BY hits DESC").fetchall()
        out = [dict(r) for r in rows]
        if query:
            # rank by intent (stem/prefix-aware), tie-break on hits; never return empty
            qtok = _tokens(query)

            def score(r):
                return rank_score(qtok, (r.get("name") or "") + " " + (r.get("role") or ""))

            ranked = sorted(out, key=lambda r: (score(r), r.get("hits", 0)), reverse=True)
            return [r for r in ranked if score(r) > 0] or ranked
        return out

    def forget(self, domain):
        with self._conn() as c:
            return c.execute("DELETE FROM selectors WHERE domain=?", (domain,)).rowcount

    # ---- action manuals: named multi-step sequences per site ----
    def save_manual(self, url, name, steps, ts=None):
        domain = _key(url)[0] if "://" in (url or "") else (url or "")
        ts = ts if ts is not None else int(time.time())
        blob = json.dumps(steps)
        with self._conn() as c:
            c.execute(
                """INSERT INTO manuals(domain, name, steps, hits, last_ts)
                   VALUES(?,?,?,1,?)
                   ON CONFLICT(domain, name) DO UPDATE SET
                     hits = hits + 1, steps = excluded.steps, last_ts = excluded.last_ts""",
                (domain, name, blob, ts))

    def get_manual(self, url=None, name=None, domain=None):
        if url and "://" in url:
            domain = _key(url)[0]
        with self._conn() as c:
            if name is not None:
                r = c.execute("SELECT * FROM manuals WHERE domain=? AND name=?",
                              (domain, name)).fetchone()
                if not r:
                    return None
                d = dict(r)
                d["steps"] = json.loads(d["steps"])
                return d
            rows = c.execute("SELECT * FROM manuals WHERE domain=? ORDER BY hits DESC",
                             (domain,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["steps"] = json.loads(d["steps"])
            out.append(d)
        return out

    def forget_manual(self, domain, name=None):
        with self._conn() as c:
            if name is not None:
                return c.execute("DELETE FROM manuals WHERE domain=? AND name=?",
                                 (domain, name)).rowcount
            return c.execute("DELETE FROM manuals WHERE domain=?", (domain,)).rowcount

    # ---- API recipes: internal API calls captured per site ----
    def record_recipe(self, url, name, method, api_url, resp_keys=None, ts=None):
        domain = _key(url)[0] if "://" in (url or "") else (url or "")
        ts = ts if ts is not None else int(time.time())
        keys = json.dumps(resp_keys or [])
        with self._conn() as c:
            c.execute(
                """INSERT INTO recipes(domain, name, method, api_url, resp_keys, hits, last_ts)
                   VALUES(?,?,?,?,?,1,?)
                   ON CONFLICT(domain, name) DO UPDATE SET
                     hits = hits + 1, method = excluded.method, api_url = excluded.api_url,
                     resp_keys = excluded.resp_keys, last_ts = excluded.last_ts""",
                (domain, name, method, api_url, keys, ts))

    def get_recipes(self, url=None, domain=None, query=None):
        if url and "://" in url:
            domain = _key(url)[0]
        with self._conn() as c:
            if domain:
                rows = c.execute("SELECT * FROM recipes WHERE domain=? ORDER BY hits DESC",
                                 (domain,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM recipes ORDER BY hits DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["resp_keys"] = json.loads(d["resp_keys"]) if d.get("resp_keys") else []
            out.append(d)
        if query:
            qtok = _tokens(query)

            def score(r):
                txt = f'{r.get("name","")} {r.get("api_url","")} {" ".join(r.get("resp_keys") or [])}'
                return rank_score(qtok, txt)

            # recipes drive direct API calls — a non-match must return EMPTY, not a misleading
            # unrelated recipe (unlike selector recall, where a best-effort fallback is benign).
            return [r for r in sorted(out, key=score, reverse=True) if score(r) > 0]
        return out

    def forget_recipe(self, domain, name=None):
        with self._conn() as c:
            if name is not None:
                return c.execute("DELETE FROM recipes WHERE domain=? AND name=?",
                                 (domain, name)).rowcount
            return c.execute("DELETE FROM recipes WHERE domain=?", (domain,)).rowcount


def format_manual(rows):
    if not rows:
        return "(nothing remembered for this site yet)"
    head = f'{rows[0]["domain"]}  ({len(rows)} elements)'
    lines = [head]
    for r in rows:
        nm = f' "{r["name"]}"' if r["name"] else ""
        lines.append(f'- {r["role"]}{nm}  -> {r["selector"]}  (hits {r["hits"]})')
    return "\n".join(lines)
