"""Human-like input: Bezier mouse paths, keystroke cadence, eased scroll.

Behavioral signals (mouse curvature, scroll acceleration, keystroke timing) are what
DataDome / Kasada weight most heavily, and they're pure userland — portable to plain
Playwright AND patchright with no custom binary. Off by default (config `humanize`),
because it adds latency; turn it on for behavioral-detection targets.

Bezier path = the ghost-cursor algorithm (cubic curve, perpendicular-offset control
points, Fitts's-law step count, overshoot-then-settle for long moves).

Improvement over the reference typist: typos are ALWAYS corrected (notice probability =
100%), so a humanized type never silently corrupts the field — correctness first.
"""
import math
import random

QWERTY_ADJ = {
    'a': 'sqzw', 'b': 'vghn', 'c': 'xdfv', 'd': 'serfcx', 'e': 'wsdr', 'f': 'drtgvc',
    'g': 'ftyhbv', 'h': 'gyujnb', 'i': 'ujko', 'j': 'huikmn', 'k': 'jiolm', 'l': 'kop',
    'm': 'njk', 'n': 'bhjm', 'o': 'iklp', 'p': 'ol', 'q': 'wa', 'r': 'edft', 's': 'awedxz',
    't': 'rfgy', 'u': 'yhji', 'v': 'cfgb', 'w': 'qase', 'x': 'zsdc', 'y': 'tghu', 'z': 'asx',
}


# ---- pure geometry ----
def _sub(a, b): return (a[0] - b[0], a[1] - b[1])
def _mag(a): return math.hypot(a[0], a[1])
def _perp(a): return (a[1], -a[0])


def _unit(a):
    m = _mag(a) or 1.0
    return (a[0] / m, a[1] / m)


def _set_mag(a, n):
    u = _unit(a)
    return (u[0] * n, u[1] * n)


def _rand_on_line(a, b, rng):
    t = rng.random()
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _anchors(a, b, spread, rng):
    side = 1 if rng.random() < 0.5 else -1
    def calc():
        mid = _rand_on_line(a, b, rng)
        nrm = _set_mag(_perp(_sub(mid, a)), spread)
        nrm = (nrm[0] * side, nrm[1] * side)
        return _rand_on_line(mid, (mid[0] + nrm[0], mid[1] + nrm[1]), rng)
    p1, p2 = calc(), calc()
    return sorted([p1, p2], key=lambda p: p[0])


def _cubic(p0, p1, p2, p3, t):
    u = 1 - t
    return (u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
            u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1])


def bezier_path(start, end, width=100, move_speed=None, spread_override=None, rng=None):
    """Sample a cubic-Bezier mouse path from start to end. Endpoints are exact."""
    rng = rng or random
    start = (float(start[0]), float(start[1]))
    end = (float(end[0]), float(end[1]))
    dist = _mag(_sub(end, start))
    spread = spread_override if spread_override is not None else max(2.0, min(200.0, dist))
    p1, p2 = _anchors(start, end, spread, rng)
    length = dist * 0.8
    fitts = 2 * math.log2(length / max(width, 1) + 1)        # Fitts's law (a=0,b=2)
    speed = (25 / move_speed) if (move_speed and move_speed > 0) else rng.random()
    base = speed * 25                                         # MIN_STEPS
    steps = max(2, math.ceil((math.log2(fitts + 1) + base) * 3))
    return [_cubic(start, p1, p2, end, i / steps) for i in range(steps + 1)]


# ---- Playwright drivers ----
async def human_move(page, x, y, start=None, width=100, rng=None):
    rng = rng or random
    start = start if start is not None else (0.0, 0.0)
    dest = (float(x), float(y))
    if _mag(_sub(dest, start)) > 500:                        # overshoot-then-settle on long moves
        a = rng.random() * 2 * math.pi
        r = 120 * math.sqrt(rng.random())                    # uniform over a disc, radius 120
        over = (dest[0] + r * math.cos(a), dest[1] + r * math.sin(a))
        for px, py in bezier_path(start, over, width, rng=rng):
            await page.mouse.move(px, py)
        for px, py in bezier_path(over, dest, width, spread_override=10, rng=rng):
            await page.mouse.move(px, py)
    else:
        for px, py in bezier_path(start, dest, width, rng=rng):
            await page.mouse.move(px, py)
    return dest


async def human_click(page, x, y, start=None, button="left", rng=None):
    rng = rng or random
    dest = await human_move(page, x, y, start=start, rng=rng)
    await page.wait_for_timeout(rng.randint(40, 140))        # hesitate before press
    await page.mouse.down(button=button)
    await page.wait_for_timeout(rng.randint(40, 120))        # press dwell
    await page.mouse.up(button=button)
    return dest


def _char_delay_ms(ch, wpm, rng):
    base = 60.0 / (wpm * 5)                                  # seconds/char at WPM (5 chars/word)
    base *= rng.uniform(0.7, 1.4)                            # jitter
    if ch == ' ':
        base += max(0.0, rng.gauss(0.25, 0.07))             # word-boundary pause
    elif ch in '.,!?;:':
        base += max(0.0, rng.gauss(0.30, 0.08))             # punctuation pause (longest)
    return max(20.0, base * 1000)


async def human_type(page, text, wpm=None, p_error=0.04, rng=None):
    """Type into the focused element with human cadence. Typos are always corrected, so
    the final value equals `text` exactly."""
    rng = rng or random
    wpm = wpm or max(35.0, rng.gauss(60, 10))
    fatigue = 1.0
    for ch in text:
        low = ch.lower()
        if low in QWERTY_ADJ and rng.random() < p_error:
            wrong = rng.choice(QWERTY_ADJ[low])
            await page.keyboard.type(wrong, delay=_char_delay_ms(ch, wpm, rng) * fatigue)
            await page.wait_for_timeout(int(max(0.0, rng.gauss(350, 90))))   # "oops" reaction
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(int(max(0.0, rng.gauss(120, 30))))   # backspace dwell
        await page.keyboard.type(ch, delay=_char_delay_ms(ch, wpm, rng) * fatigue)
        fatigue *= 1.0005                                    # slight slowdown over time


def _smoothstep(t):
    return t * t * (3 - 2 * t)


async def human_scroll(page, dy, steps=None, rng=None):
    rng = rng or random
    steps = steps or max(15, min(60, int(abs(dy) / 100) or 15))
    sign = 1 if dy > 0 else -1
    prev = 0.0
    for i in range(1, steps + 1):
        eased = _smoothstep(i / steps) * abs(dy)            # accel -> cruise -> decel
        d = (eased - prev) * sign
        prev = eased
        await page.mouse.wheel(0, d * rng.uniform(0.9, 1.1))
        edge = min(i, steps - i) / steps                    # slower at the ends
        await page.wait_for_timeout(int(8 + (1 - _smoothstep(min(1.0, edge * 2))) * 8))
    await page.wait_for_timeout(rng.randint(120, 260))      # settle
