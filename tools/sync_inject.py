"""Single source of truth for the junk blocklist used by the page-side cleaners.

The bridge runs CLEAN_FN inside extension/background.js; the managed browser runs
CLEAN_HTML_JS from roam/markdown.py. Both must use the IDENTICAL blocklist, or the same
page yields different markdown depending on the backend. Rather than maintain the list
twice, this script writes the Python `_JUNK_LIST` into the `const junk = "..."` line of
background.js CLEAN_FN (marked `@generated-from`).

Run after editing _JUNK_LIST:  py tools/sync_inject.py
The parity test (tests/test_clean_parity.py) fails CI if they drift.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
BG = ROOT / "extension" / "background.js"


def junk_csv():
    from roam.markdown import _JUNK_LIST
    return ",".join(_JUNK_LIST)


def apply():
    csv = junk_csv()
    text = BG.read_text(encoding="utf-8")
    # replace the placeholder or a previously-synced literal inside CLEAN_FN's junk const.
    # single-quote the wrapper because the selectors themselves contain double quotes
    # (e.g. [role="navigation"]).
    new = re.sub(r"const junk = '[^']*';",
                 f"const junk = '{csv}';", text, count=1)
    if new == text and "__JS_JUNK__" not in text:
        print("sync_inject: no change (already in sync)")
        return False
    BG.write_text(new, encoding="utf-8", newline="\n")
    print(f"sync_inject: wrote {len(csv.split(','))} junk selectors into CLEAN_FN")
    return True


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(ROOT))
    apply()
