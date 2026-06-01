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


def format_manual(rows):
    if not rows:
        return "(nothing remembered for this site yet)"
    head = f'{rows[0]["domain"]}  ({len(rows)} elements)'
    lines = [head]
    for r in rows:
        nm = f' "{r["name"]}"' if r["name"] else ""
        lines.append(f'- {r["role"]}{nm}  -> {r["selector"]}  (hits {r["hits"]})')
    return "\n".join(lines)
