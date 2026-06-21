"""Dismiss the cookie/consent/newsletter popups that gate real-world sites (investor-
relations pages especially), and find links by intent (filings, investor decks, reports).
Combines the tactics across the reference tools: click the known consent widgets + any
accept/close button, then strip leftover full-screen overlays and restore scroll.
"""

DISMISS_JS = r"""
() => {
  const clicked = [];
  // 1. known consent widgets (OneTrust / Cookiebot / TrustArc / Sourcepoint / Quantcast / HubSpot)
  const known = [
    '#onetrust-accept-btn-handler', '#onetrust-reject-all-handler', '#truste-consent-button',
    '#hs-eu-confirmation-button', '.cc-allow', '.cc-dismiss', '#CybotCookiebotDialogBodyButtonAccept',
    '.fc-button.fc-cta-consent', '[data-testid*="accept" i]', '[aria-label="Accept all"]',
    '[aria-label="Close"]', '[title="Close"]', 'button[mode="primary"]'
  ];
  const shown = (el) => el && (el.offsetParent !== null || el.getClientRects().length > 0);
  for (const sel of known) {
    try { const el = document.querySelector(sel); if (shown(el)) { el.click(); clicked.push(sel); } } catch (e) {}
  }
  // 2. any button/link whose label is a consent/close affordance
  const re = /^(accept|accept all|accept cookies|agree|i agree|got it|i understand|ok|okay|continue|close|dismiss|no thanks|reject all|allow all|that.?s ok|x)$/i;
  document.querySelectorAll('button, a, [role="button"], input[type="button"], input[type="submit"]').forEach(b => {
    const t = (b.innerText || b.value || b.getAttribute('aria-label') || '').trim();
    if (re.test(t)) { try { b.click(); clicked.push(t); } catch (e) {} }
  });
  // 3. remove leftover fixed/sticky full-screen overlays
  let removed = 0;
  document.querySelectorAll('div,section,aside,dialog,[class*="modal" i],[class*="popup" i],[class*="overlay" i]').forEach(e => {
    const cs = getComputedStyle(e); const z = parseInt(cs.zIndex) || 0;
    if ((cs.position === 'fixed' || cs.position === 'sticky') && z >= 100 &&
        e.offsetHeight > window.innerHeight * 0.5 && e.offsetWidth > window.innerWidth * 0.5) { e.remove(); removed++; }
  });
  // 4. restore scroll (popups lock it)
  for (const el of [document.documentElement, document.body]) {
    if (el) { el.style.setProperty('overflow', 'auto', 'important'); el.style.setProperty('position', 'static', 'important');
      el.classList.remove('no-scroll', 'noscroll', 'modal-open', 'overflow-hidden', 'fixed'); }
  }
  return { clicked: clicked.slice(0, 12), removed };
}
"""

FIND_LINKS_JS = r"""
(keywords) => {
  const kw = (keywords || []).map(k => String(k).toLowerCase());
  const seen = new Set(); const out = [];
  document.querySelectorAll('a[href]').forEach(a => {
    let href; try { href = new URL(a.getAttribute('href'), location.href).href; } catch (e) { return; }
    if (href.startsWith('javascript:') || href.startsWith('mailto:') || seen.has(href)) return;
    seen.add(href);
    const text = (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const hay = (text + ' ' + href).toLowerCase();
    if (!kw.length || kw.some(k => hay.includes(k))) out.push({ text, href });
  });
  return out.slice(0, 120);
}
"""
