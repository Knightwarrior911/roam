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
