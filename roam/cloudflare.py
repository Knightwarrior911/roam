"""Cloudflare Turnstile / interstitial solver.

Adapted from Scrapling's coordinate-click approach, with deliberate improvements over a
blind port:
  - BOUNDED attempts (Scrapling recurses with no cap -> risk of an infinite loop).
  - the checkbox click is computed RELATIVE to the widget box (vertical center +
    left-side offset) instead of a hardcoded absolute (26-28, 25-27), so it adapts to
    the widget size and doesn't miss when the layout differs.
  - clicks can be HUMANIZED (Bezier move + dwell) when the caller passes a click_fn.
  - iterative loop, not recursion; clean pages return instantly (no mandatory settle wait).

It is NOT a captcha-breaking service: it only satisfies the auto/clickable Turnstile
challenge that a sufficiently non-bot browser is offered. The honest strong path remains
the bridge (a real browser passes Cloudflare natively).
"""
import re
import random

_CF_IFRAME = re.compile(r"challenges\.cloudflare\.com/cdn-cgi/challenge-platform")
_CHALLENGE_TYPES = ("non-interactive", "managed", "interactive")


def detect_challenge(html):
    """Return the Cloudflare challenge type present in page HTML, or None."""
    if not html:
        return None
    for t in _CHALLENGE_TYPES:
        if f"cType: '{t}'" in html or f'cType: "{t}"' in html:
            return t
    if "challenges.cloudflare.com/turnstile/v" in html:
        return "embedded"
    if "Just a moment" in html and ("cf-" in html or "cloudflare" in html.lower()):
        return "managed"
    return None


async def _turnstile_box(page):
    # the challenge runs in a cross-origin iframe; find it by URL and get its viewport box
    for fr in page.frames:
        try:
            if _CF_IFRAME.search(fr.url or ""):
                fe = await fr.frame_element()
                box = await fe.bounding_box()
                if box:
                    return box
        except Exception:
            pass
    # fallback: an embedded turnstile container in the main document
    try:
        loc = page.locator("#cf-turnstile, .cf-turnstile, #cf_turnstile, .turnstile").first
        if await loc.count():
            return await loc.bounding_box()
    except Exception:
        pass
    return None


async def _wait_gone(page, poll_ms=500, timeout_ms=12000):
    waited = 0
    while waited < timeout_ms:
        await page.wait_for_timeout(poll_ms)
        waited += poll_ms
        try:
            html = await page.content()
        except Exception:
            continue
        if not detect_challenge(html):
            return True
    return False


async def solve(page, click_fn=None, max_attempts=3, settle_ms=4000,
                poll_ms=500, poll_timeout_ms=12000):
    """Try to clear a Cloudflare challenge on the current page. Returns
    {solved: bool, attempts: int, type: str|None}."""
    attempts = 0
    last_type = None
    while attempts < max_attempts:
        try:
            html = await page.content()
        except Exception:
            html = ""
        ctype = detect_challenge(html)
        last_type = ctype
        if not ctype:
            return {"solved": True, "attempts": attempts, "type": None}

        attempts += 1
        # give the widget a moment to initialize before acting
        await page.wait_for_timeout(settle_ms)

        if ctype == "non-interactive":
            if await _wait_gone(page, poll_ms, poll_timeout_ms):
                return {"solved": True, "attempts": attempts, "type": ctype}
            continue

        box = await _turnstile_box(page)
        if not box:
            # maybe it cleared on its own while we settled
            if await _wait_gone(page, poll_ms, 3000):
                return {"solved": True, "attempts": attempts, "type": ctype}
            continue

        # checkbox sits on the left, vertically centered — relative to the widget box
        cx = box["x"] + min(30, box["width"] * 0.12) + random.uniform(-2, 2)
        cy = box["y"] + box["height"] / 2 + random.uniform(-2, 2)
        if click_fn:
            await click_fn(cx, cy)
        else:
            await page.mouse.click(cx, cy, delay=random.randint(90, 170))

        if await _wait_gone(page, poll_ms, poll_timeout_ms):
            return {"solved": True, "attempts": attempts, "type": ctype}

    return {"solved": False, "attempts": attempts, "type": last_type}
