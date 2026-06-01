import json
import os
import sqlite3
import time
from urllib.parse import urlparse


def _key(url):
    u = urlparse(url)
    return u.netloc, (u.path or "/")


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
  const role = el.getAttribute('role') || el.tagName.toLowerCase();
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
                    hits INTEGER DEFAULT 1, last_ts INTEGER,
                    PRIMARY KEY(domain, path, role, name))"""
            )
            # action manuals: a named, ordered sequence of steps per site (the moat).
            c.execute(
                """CREATE TABLE IF NOT EXISTS manuals(
                    domain TEXT, name TEXT, steps TEXT,
                    hits INTEGER DEFAULT 1, last_ts INTEGER,
                    PRIMARY KEY(domain, name))"""
            )

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def record(self, url, role, name, selector, ts=None):
        if not selector:
            return
        domain, path = _key(url)
        ts = ts if ts is not None else int(time.time())
        with self._conn() as c:
            c.execute(
                """INSERT INTO selectors(domain, path, role, name, selector, hits, last_ts)
                   VALUES(?,?,?,?,?,1,?)
                   ON CONFLICT(domain, path, role, name) DO UPDATE SET
                     hits = hits + 1, selector = excluded.selector, last_ts = excluded.last_ts""",
                (domain, path, role, name, selector, ts),
            )

    def recall(self, url=None, domain=None):
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
        return [dict(r) for r in rows]

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


def format_manual(rows):
    if not rows:
        return "(nothing remembered for this site yet)"
    head = f'{rows[0]["domain"]}  ({len(rows)} elements)'
    lines = [head]
    for r in rows:
        nm = f' "{r["name"]}"' if r["name"] else ""
        lines.append(f'- {r["role"]}{nm}  -> {r["selector"]}  (hits {r["hits"]})')
    return "\n".join(lines)
