# Visual cue for a tab Roam is controlling.
#
# Two-layer design (improves on actionbook, which only does a native tab-group cue):
#   - native Chrome tab group  -> tab-strip cue (done in the extension, chrome.tabGroups)
#   - in-page border + badge    -> CUE_JS below, injected into the page DOM
#
# Hard requirements baked into the JS (and pinned by tests):
#   - lives in a CLOSED shadow root on a host attached to <html> (not <body>), so the
#     badge text never leaks into document.body.innerText / snapshot / read_markdown.
#   - pointer-events: none everywhere, so it never intercepts a user OR automation click.
#   - idempotent: re-invoking updates in place (one host only).
#
# The extension ships the same logic (extension/background.js CUE_FN) for the bridge path.
CUE_JS = r"""
(args) => {
  const HOST_ID = '__roam_cue_host__';
  const root = document.documentElement || document.body;
  const existing = document.getElementById(HOST_ID);
  if (existing) existing.remove();
  if (!args || !args.on) return { shown: false };
  if (!root) return { shown: false };

  const color = args.color || '#6c5ce7';
  const label = args.label || 'Roam controlling';

  const host = document.createElement('div');
  host.id = HOST_ID;
  // host itself is inert and out of layout; the cue is painted inside its shadow root
  host.style.cssText = 'all:initial;position:fixed;inset:0;pointer-events:none;z-index:2147483647';
  const shadow = host.attachShadow ? host.attachShadow({ mode: 'closed' }) : host;

  const style = document.createElement('style');
  style.textContent =
    ':host,*{pointer-events:none!important;box-sizing:border-box}' +
    '.border{position:fixed;inset:0;border:3px solid ' + color + ';border-radius:6px;' +
      'box-shadow:0 0 0 1px rgba(0,0,0,.25) inset}' +
    '.badge{position:fixed;top:10px;right:10px;display:flex;align-items:center;gap:7px;' +
      'font:600 12px/1.2 -apple-system,Segoe UI,Roboto,sans-serif;color:#fff;' +
      'background:' + color + ';padding:6px 11px;border-radius:999px;' +
      'box-shadow:0 2px 8px rgba(0,0,0,.35);white-space:nowrap}' +
    '.dot{width:8px;height:8px;border-radius:50%;background:#fff;opacity:.95;' +
      'animation:roampulse 1.2s ease-in-out infinite}' +
    '@keyframes roampulse{0%,100%{opacity:.35;transform:scale(.8)}50%{opacity:1;transform:scale(1.15)}}';

  const border = document.createElement('div');
  border.className = 'border';

  const badge = document.createElement('div');
  badge.className = 'badge';
  const dot = document.createElement('span');
  dot.className = 'dot';
  const text = document.createElement('span');
  text.textContent = '🤖 ' + label;   // 🤖 label
  badge.appendChild(dot);
  badge.appendChild(text);

  shadow.appendChild(style);
  shadow.appendChild(border);
  shadow.appendChild(badge);
  root.appendChild(host);
  return { shown: true };
}
"""
