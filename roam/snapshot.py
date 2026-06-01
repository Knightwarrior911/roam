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
  const out = [];
  let n = 0;
  const walk = (el) => {
    for (const child of el.children) {
      const vis = child.offsetParent !== null || child.tagName === 'OPTION';
      if (vis && (!interactiveOnly || isInteractive(child))) {
        n += 1;
        const ref = 'e' + n;
        child.setAttribute('data-roam-ref', ref);
        out.push({ ref, role: roleOf(child), name: nameOf(child) });
      }
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
        lines.append(f'- {node["role"]}{nm} [ref={node["ref"]}]')
    return "\n".join(lines) if lines else "(no elements found)"
