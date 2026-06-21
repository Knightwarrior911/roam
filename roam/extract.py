# Deterministic, schema-driven structured extraction (Firecrawl-style "extract", but
# selector-driven instead of LLM-driven — Claude supplies the selectors from a snapshot,
# Roam does the fast, reusable, exact pull). Runs as one page.evaluate so it also works
# over the bridge.
#
# fields: { name: spec }   where spec is either a CSS selector string (-> trimmed text)
#          or { selector, attr?, all? }:
#            attr  -> read that attribute ("text" forces text; default = text)
#            all   -> return a list of every match instead of the first
# item:    optional container selector; when set, the result is a LIST with `fields`
#          extracted per matching container (relative to it).
EXTRACT_JS = r"""
(args) => {
  const fields = args.fields || {};
  const item = args.item || null;
  const readEl = (el, attr) => {
    if (!el) return null;
    if (attr && attr !== 'text') {
      if (attr === 'href' || attr === 'src') {
        try { return new URL(el.getAttribute(attr), location.href).href; } catch (e) {}
      }
      return el.getAttribute(attr);
    }
    return (el.innerText || el.textContent || '').trim();
  };
  const getOne = (root, spec) => {
    const sel = (typeof spec === 'string') ? spec : spec.selector;
    const attr = (typeof spec === 'object') ? spec.attr : null;
    const all = (typeof spec === 'object') ? !!spec.all : false;
    if (!sel) return null;
    if (all) return Array.from(root.querySelectorAll(sel)).map(e => readEl(e, attr));
    return readEl(root.querySelector(sel), attr);
  };
  const extractFrom = (root) => {
    const o = {};
    for (const k in fields) o[k] = getOne(root, fields[k]);
    return o;
  };
  if (item) return Array.from(document.querySelectorAll(item)).map(extractFrom);
  return extractFrom(document);
}
"""

# Auto-schema: find the largest group of structurally-similar repeating siblings (product
# cards, search results, table rows) WITHOUT the agent pre-writing selectors, infer a field
# name + value per leaf, and infer a coarse type. Runs as one page.evaluate (bridge-safe).
AUTO_EXTRACT_JS = r"""
(args) => {
  const maxItems = (args && args.maxItems) || 30;
  const rootSel = (args && args.itemSelector) || null;
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return u; } };
  const sig = (el) => el.tagName + ':' + el.className + ':' + el.children.length;
  let group = null;
  if (rootSel) {
    group = Array.from(document.querySelectorAll(rootSel));
  } else {
    // pick the parent whose children form the largest uniform-signature run with real text
    const root = document.querySelector('main') || document.querySelector('[role="main"]') ||
                 document.querySelector('article') || document.body;
    let best = null, bestScore = 0;
    const consider = (parent) => {
      const buckets = {};
      for (const c of parent.children) {
        const s = sig(c);
        (buckets[s] = buckets[s] || []).push(c);
      }
      for (const s in buckets) {
        const arr = buckets[s];
        if (arr.length < 3) continue;
        const textLen = arr.reduce((a, e) => a + (e.innerText || '').length, 0);
        const score = arr.length * Math.min(textLen, 4000);
        if (score > bestScore) { bestScore = score; best = arr; }
      }
    };
    const stack = [root];
    let guard = 0;
    while (stack.length && guard < 4000) {
      const node = stack.pop(); guard++;
      consider(node);
      for (const c of node.children) stack.push(c);
    }
    group = best || [];
  }
  group = group.slice(0, maxItems);
  // infer fields from the FIRST item's labeled leaves; reuse the chosen selectors on the rest
  const typeOf = (v) => {
    if (v == null) return 'null';
    if (/^[-+]?\$?\d[\d,]*(\.\d+)?%?$/.test(v.trim())) return 'number';
    if (/^https?:\/\//.test(v.trim())) return 'url';
    if (/^\d{4}-\d{2}-\d{2}/.test(v.trim()) || /\b\d{1,2}\/\d{1,2}\/\d{2,4}\b/.test(v)) return 'date';
    return 'string';
  };
  const fieldName = (el, i) => (
    el.getAttribute('aria-label') || el.getAttribute('itemprop') ||
    el.getAttribute('data-testid') || el.getAttribute('name') ||
    (el.tagName === 'A' ? 'link' : (el.tagName === 'IMG' ? 'image' : '')) ||
    ('field' + i)
  ).replace(/\s+/g, '_').toLowerCase();
  const leafSpecs = [];
  if (group.length) {
    const first = group[0];
    const leaves = Array.from(first.querySelectorAll('a,img,h1,h2,h3,h4,span,p,td,th,[itemprop],[aria-label]'));
    const seen = new Set();
    let idx = 0;
    for (const leaf of leaves) {
      const txt = leaf.tagName === 'IMG' ? abs(leaf.getAttribute('src')) :
                  leaf.tagName === 'A' ? (leaf.innerText || '').trim() :
                  (leaf.innerText || leaf.textContent || '').trim();
      if (!txt) continue;
      const nm = fieldName(leaf, idx);
      if (seen.has(nm)) continue;
      seen.add(nm); idx++;
      // build a simple relative selector: tag + first class
      let sel = leaf.tagName.toLowerCase();
      if (leaf.className && typeof leaf.className === 'string') {
        const cls = leaf.className.trim().split(/\s+/)[0];
        if (cls) sel += '.' + CSS.escape(cls);
      }
      // anchors with visible text -> capture the TEXT (the human-facing name); image -> src
      const hasText = (leaf.innerText || '').trim().length > 0;
      const attr = leaf.tagName === 'IMG' ? 'src' : (leaf.tagName === 'A' && !hasText ? 'href' : 'text');
      leafSpecs.push({ name: nm, selector: sel, attr, sampleType: typeOf(txt) });
      if (leafSpecs.length >= 12) break;
    }
  }
  const readEl = (el, attr) => {
    if (!el) return null;
    if (attr === 'href' || attr === 'src') { try { return new URL(el.getAttribute(attr), location.href).href; } catch (e) { return el.getAttribute(attr); } }
    return (el.innerText || el.textContent || '').trim();
  };
  const data = group.map(it => {
    const o = {};
    for (const f of leafSpecs) {
      const el = it.querySelector(f.selector);
      o[f.name] = el ? readEl(el, f.attr) : null;
    }
    return o;
  });
  const schema = {};
  for (const f of leafSpecs) schema[f.name] = f.sampleType;
  return { schema, count: data.length, fields: leafSpecs.map(f => f.name), data };
}
"""

# Pull structured data already in the page (Firecrawl's "collect it correctly" pattern):
# JSON-LD > schema.org microdata > OpenGraph/meta, merged into one candidate map. No LLM.
STRUCTURED_DATA_JS = r"""
() => {
  const out = {};
  const put = (k, v) => { if (v != null && v !== '' && !(k in out)) out[k] = v; };
  // JSON-LD (highest priority)
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      let j = JSON.parse(s.textContent);
      const arr = Array.isArray(j) ? j : (j['@graph'] ? j['@graph'] : [j]);
      for (const obj of arr) {
        if (!obj || typeof obj !== 'object') continue;
        for (const k of ['name','headline','description','image','price','brand','author','datePublished','sku','url']) {
          let v = obj[k];
          if (v && typeof v === 'object') v = v.name || v['@id'] || v.url || v.price || null;
          put(k, v);
        }
        if (obj.offers && obj.offers.price) put('price', obj.offers.price);
      }
    } catch (e) {}
  }
  // microdata
  document.querySelectorAll('[itemprop]').forEach(el => {
    const k = el.getAttribute('itemprop');
    const v = el.getAttribute('content') || (el.tagName==='IMG'?el.src:(el.tagName==='A'?el.href:(el.innerText||'').trim()));
    if (k) put(k, v);
  });
  // OpenGraph / meta
  document.querySelectorAll('meta[property^="og:"],meta[name^="og:"],meta[name="description"],meta[name="author"]').forEach(m => {
    const k = (m.getAttribute('property') || m.getAttribute('name') || '').replace(/^og:/, '');
    put(k, m.getAttribute('content'));
  });
  if (!out.title) put('title', document.title);
  return out;
}
"""
