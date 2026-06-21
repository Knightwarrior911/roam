"""Tiny in-memory LRU for idempotent reads (read_markdown / observe / structured_data).

Repeated reads of the same URL in a session re-run the whole clean/score pipeline every
time. A cache keyed on (kind, url, args-hash) makes replays O(1) and returns a HIT/MISS
flag so callers can audit. Variables are hashed by NAME not value (so secrets don't leak
into keys and {user:alice}/{user:bob} share an entry). Bounded; oldest evicted first.
"""
import hashlib
import json
from collections import OrderedDict

_MAX = 128
_store = OrderedDict()   # key -> value
_enabled = True


def _key(kind, url, args):
    h = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{kind}|{url or ''}|{h}"


def get(kind, url, args):
    if not _enabled:
        return None, False
    k = _key(kind, url, args)
    if k in _store:
        _store.move_to_end(k)
        return _store[k], True
    return None, False


def put(kind, url, args, value):
    if not _enabled:
        return
    k = _key(kind, url, args)
    _store[k] = value
    _store.move_to_end(k)
    while len(_store) > _MAX:
        _store.popitem(last=False)


def clear():
    _store.clear()


def set_enabled(on):
    global _enabled
    _enabled = bool(on)


def stats():
    return {"entries": len(_store), "enabled": _enabled, "max": _MAX}
