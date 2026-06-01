"""Self-healing selectors (the moat). When a cached selector stops matching because a site
changed, relocate the element by structural similarity to a stored fingerprint, then rewrite
the cached selector so the next visit is a cheap direct hit. The idea is Scrapling's: average
many weak signals (tag, text, attrs, ancestor path, parent, children) so no single change
breaks it. No ML, no embeddings.
"""

# Capture a redundant fingerprint of an element (by ref or css selector).
FINGERPRINT_JS = r"""
(args) => {
  const el = args.ref ? document.querySelector('[data-roam-ref="' + args.ref + '"]')
                      : document.querySelector(args.selector);
  if (!el) return null;
  const attrs = {};
  for (const a of el.attributes) if (a.name !== 'data-roam-ref') attrs[a.name] = a.value;
  const path = []; let p = el;
  while (p && p.tagName && p.tagName !== 'BODY' && path.length < 8) { path.unshift(p.tagName.toLowerCase()); p = p.parentElement; }
  const par = el.parentElement;
  return {
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.textContent || '').trim().slice(0, 120),
    attrs, path,
    parent_tag: par ? par.tagName.toLowerCase() : '',
    siblings: par ? [...par.children].map(c => c.tagName.toLowerCase()) : [],
    children: [...el.children].map(c => c.tagName.toLowerCase()),
  };
}
"""

# Fingerprint a Playwright Locator's element directly (used at record time).
FINGERPRINT_EL_JS = r"""
(el) => {
  if (!el) return null;
  const attrs = {};
  for (const a of el.attributes) if (a.name !== 'data-roam-ref') attrs[a.name] = a.value;
  const path = []; let p = el;
  while (p && p.tagName && p.tagName !== 'BODY' && path.length < 8) { path.unshift(p.tagName.toLowerCase()); p = p.parentElement; }
  const par = el.parentElement;
  return {
    tag: el.tagName.toLowerCase(),
    text: (el.innerText || el.textContent || '').trim().slice(0, 120),
    attrs, path,
    parent_tag: par ? par.tagName.toLowerCase() : '',
    siblings: par ? [...par.children].map(c => c.tagName.toLowerCase()) : [],
    children: [...el.children].map(c => c.tagName.toLowerCase()),
  };
}
"""

# Given a fingerprint, score every element, tag the best with data-roam-ref="heal",
# and return its freshly-derived durable selector + score (null if nothing clears the bar).
RELOCATE_JS = r"""
(fp) => {
  const bigrams = (s) => { s = String(s || ''); const m = {}; for (let i = 0; i < s.length - 1; i++) { const g = s.slice(i, i + 2); m[g] = (m[g] || 0) + 1; } return m; };
  const sim = (a, b) => { a = String(a || ''); b = String(b || ''); if (!a && !b) return 1; if (!a || !b) return 0;
    const A = bigrams(a), B = bigrams(b); let inter = 0, ta = 0, tb = 0;
    for (const k in A) { ta += A[k]; if (B[k]) inter += Math.min(A[k], B[k]); }
    for (const k in B) tb += B[k]; return (2 * inter) / ((ta + tb) || 1); };
  const arrSim = (a, b) => sim((a || []).join(','), (b || []).join(','));
  const score = (el) => {
    const attrs = {}; for (const a of el.attributes) attrs[a.name] = a.value;
    const path = []; let p = el;
    while (p && p.tagName && p.tagName !== 'BODY' && path.length < 8) { path.unshift(p.tagName.toLowerCase()); p = p.parentElement; }
    const fa = fp.attrs || {};
    let s = 0, n = 0;
    s += (el.tagName.toLowerCase() === fp.tag ? 1 : 0); n++;
    s += sim(el.innerText || el.textContent || '', fp.text); n++;
    s += sim(attrs.class || '', fa.class || ''); n++;
    s += (attrs.id && fa.id && attrs.id === fa.id) ? 1 : sim(attrs.id || '', fa.id || ''); n++;
    s += arrSim(path, fp.path); n++;
    s += arrSim([...el.children].map(c => c.tagName.toLowerCase()), fp.children); n++;
    return s / n;
  };
  let best = null, bestScore = 0;
  for (const el of document.querySelectorAll('*')) { const sc = score(el); if (sc > bestScore) { bestScore = sc; best = el; } }
  if (!best || bestScore < 0.5) return { score: Math.round(bestScore * 100) / 100, selector: null };
  const durable = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = []; let node = el;
    while (node && node.tagName && node.tagName !== 'BODY' && parts.length < 5) {
      let sel = node.tagName.toLowerCase(); const par = node.parentElement;
      if (par) { const sib = [...par.children].filter(c => c.tagName === node.tagName); if (sib.length > 1) sel += ':nth-of-type(' + (sib.indexOf(node) + 1) + ')'; }
      parts.unshift(sel); node = par;
    }
    return parts.join(' > ');
  };
  document.querySelectorAll('[data-roam-ref="heal"]').forEach(e => e.removeAttribute('data-roam-ref'));
  best.setAttribute('data-roam-ref', 'heal');
  return { score: Math.round(bestScore * 100) / 100, selector: durable(best) };
}
"""
