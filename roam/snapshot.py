# Tags interactive/named DOM nodes with data-roam-ref="eN" and returns
# [{ref, role, name}] so tools can resolve a ref to a Playwright locator.
SNAPSHOT_JS = r"""
(args) => {
  const interactiveOnly = args.interactiveOnly;
  const rootSel = args.rootSelector;
  document.querySelectorAll('[data-roam-ref]').forEach(e => e.removeAttribute('data-roam-ref'));
  const root = rootSel ? document.querySelector(rootSel) : document.body;
  if (!root) return [];
  const INTERACTIVE = new Set(['A','BUTTON','INPUT','TEXTAREA','SELECT','OPTION','SUMMARY']);
  const isInteractive = (el) => {
    if (INTERACTIVE.has(el.tagName)) return true;
    const r = el.getAttribute('role');
    if (r && ['button','link','textbox','checkbox','radio','tab','menuitem'].includes(r)) return true;
    if (el.hasAttribute('onclick') || el.tabIndex >= 0) return true;
    return false;
  };
  const roleOf = (el) => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName;
    if (t === 'A') return 'link';
    if (t === 'BUTTON') return 'button';
    if (t === 'SELECT') return 'combobox';
    if (t === 'TEXTAREA') return 'textbox';
    if (t === 'INPUT') return (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox';
    return t.toLowerCase();
  };
  const nameOf = (el) => (
    el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
    el.getAttribute('alt') || (el.value && el.type !== 'password' ? el.value : '') ||
    (el.innerText || el.textContent || '').trim()
  ).replace(/\s+/g, ' ').slice(0, 120);
  const vh = window.innerHeight || 0;
  const viewOf = (el) => {
    const r = el.getBoundingClientRect();
    if (r.bottom < 0) return 'above';
    if (r.top > vh) return 'below';
    return 'in';
  };
  // offsetParent is null for position:fixed AND for sticky-in-some-cases, so fixed headers,
  // modals, cookie bars, sticky toolbars (every modern web app) would be invisible. Use
  // getClientRects()/computed position so they are seen. (matches Playwright's isVisible.)
  const isVisible = (el) => {
    if (el.tagName === 'OPTION') return true;
    if (el.offsetParent !== null) return true;
    if (el.getClientRects().length > 0) return true;
    const pos = getComputedStyle(el).position;
    return pos === 'fixed' || pos === 'sticky';
  };
  const out = [];
  let n = 0;
  const prefix = (args && args.refPrefix) || '';
  const walk = (el) => {
    for (const child of el.children) {
      const vis = isVisible(child);
      if (vis && (!interactiveOnly || isInteractive(child))) {
        n += 1;
        const ref = prefix + 'e' + n;
        child.setAttribute('data-roam-ref', ref);
        out.push({ ref, role: roleOf(child), name: nameOf(child), view: viewOf(child) });
      }
      // descend OPEN shadow roots — the whole SaaS surface (Lit/FAST web components,
      // YouTube player, many design-system buttons) lives there. Playwright's locator
      // pierces open shadow, so a stamped data-roam-ref still resolves for click/type.
      if (child.shadowRoot) walk(child.shadowRoot);
      walk(child);
    }
  };
  walk(root);
  return out;
}
"""


def build_outline(nodes):
    lines = []
    for node in nodes:
        nm = f' "{node["name"]}"' if node.get("name") else ""
        mark = "" if node.get("view", "in") == "in" else f' ({node["view"]})'
        lines.append(f'- {node["role"]}{nm}{mark} [ref={node["ref"]}]')
    return "\n".join(lines) if lines else "(no elements found)"
