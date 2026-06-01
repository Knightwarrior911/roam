"""Import a site's session cookies from a local Chromium browser (Chrome/Edge) into
Roam's profile, so Roam browses as the logged-in you. Everything stays on this machine:
the cookie-encryption key is unwrapped with Windows DPAPI (same user), values are
AES-GCM decrypted, and only the requested domain's cookies are read.
"""
import base64
import json
import os
import shutil
import sqlite3
import tempfile

BROWSERS = {
    "edge": ("Microsoft", "Edge"),
    "chrome": ("Google", "Chrome"),
}


def _user_data_dir(browser):
    vendor, name = BROWSERS[browser]
    return os.path.join(os.environ["LOCALAPPDATA"], vendor, name, "User Data")


def _aes_key(user_data_dir):
    import win32crypt
    ls = os.path.join(user_data_dir, "Local State")
    blob = json.load(open(ls, encoding="utf-8"))["os_crypt"]["encrypted_key"]
    raw = base64.b64decode(blob)
    if raw[:5] == b"DPAPI":
        return win32crypt.CryptUnprotectData(raw[5:], None, None, None, 0)[1]
    raise RuntimeError("app-bound cookie encryption (v20) not supported")


def _decrypt(value, key):
    if not value:
        return ""
    value = bytes(value)
    if value[:3] in (b"v10", b"v11"):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce, ct = value[3:15], value[15:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", "ignore")
    try:
        import win32crypt
        return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1].decode("utf-8", "ignore")
    except Exception:
        return ""


def read_cookies(browser, domain_like):
    """Return Playwright-shaped cookies for hosts matching domain_like, from the browser's
    Default profile. Raises on missing browser / app-bound encryption."""
    ud = _user_data_dir(browser)
    if not os.path.isdir(ud):
        raise RuntimeError(f"{browser} not found at {ud}")
    key = _aes_key(ud)
    db = os.path.join(ud, "Default", "Network", "Cookies")
    tmp = os.path.join(tempfile.gettempdir(), f"_roam_ck_{browser}.db")
    shutil.copy2(db, tmp)              # copy so a running browser's lock doesn't block us
    try:
        con = sqlite3.connect(tmp)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT host_key,name,encrypted_value,path,expires_utc,is_secure,is_httponly,samesite "
            "FROM cookies WHERE host_key LIKE ?", (f"%{domain_like}%",)).fetchall()
        con.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    samesite = {0: "None", 1: "Lax", 2: "Strict"}
    out = []
    for r in rows:
        val = _decrypt(r["encrypted_value"], key)
        if not val:
            continue
        c = {"name": r["name"], "value": val, "domain": r["host_key"],
             "path": r["path"] or "/", "secure": bool(r["is_secure"]),
             "httpOnly": bool(r["is_httponly"]), "sameSite": samesite.get(r["samesite"], "Lax")}
        exp = r["expires_utc"]          # Chrome epoch: microseconds since 1601-01-01
        if exp and exp > 0:
            unix = exp / 1_000_000 - 11644473600
            if unix > 0:
                c["expires"] = unix
        out.append(c)
    return out
