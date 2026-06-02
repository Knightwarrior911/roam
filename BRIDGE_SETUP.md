# Roam Bridge — drive your real, logged-in browser

The bridge lets Roam drive the browser **you already use** (Comet / Chrome / Edge) instead
of a fresh automated one. That browser has your logins, your sessions, and your extensions
(Bypass Paywalls Clean), and it isn't flagged as automation, so hard sites (Bloomberg, etc.)
work exactly as they do for you. This is the same approach actionbook uses, now your own.

Browsers deliberately block silent extension installs, so the **one-time install is manual**
(about 30 seconds). After that, Roam connects automatically every time.

## One-time install (~30 seconds)

The extension is a standard Chromium MV3 extension, so it loads the same way in
**Chrome, Edge, and Comet** (all Chromium). Pick your browser's extensions page:

| Browser | Extensions page | Developer-mode toggle |
|---------|-----------------|-----------------------|
| Chrome  | `chrome://extensions` | top-right |
| Comet   | `chrome://extensions` | top-right |
| Edge    | `edge://extensions`   | bottom-left sidebar |

1. Open the extensions page above for your browser.
2. Turn on **Developer mode**.
3. Click **Load unpacked**.
4. Select the folder: `C:\Users\vinit\roam\extension`
5. "Roam Bridge" appears in the list. Done — it stays installed.

You can install it in more than one browser; whichever one is running connects to the
bridge. (Only one browser should be connected at a time — the bridge is newest-connection-wins.)

## Use it

1. Start the bridge (either way):
   - In Claude Code, have the agent call the **`bridge`** tool (starts the server in-process), or
   - Run a standalone bridge daemon: `python -m roam.bridge`
2. The extension auto-connects within a second or two. Check with the **`bridge_status`** tool
   (`connected: true`).
3. Now every Roam browser tool — `goto`, `snapshot`, `click`, `type`, `read`, `eval`,
   `screenshot`, `tabs`, `back`/`forward`/`reload` — drives your **active real tab**.
   When the bridge isn't connected, Roam falls back to its own managed browser automatically.

## Reliability

The extension auto-reconnects with backoff and runs a ping/pong heartbeat; if the connection
drops it re-establishes on its own (an open WebSocket also keeps the MV3 service worker alive
on Chrome 116+, and a keep-alive alarm covers the gaps). The Roam side fails any in-flight
call cleanly on a drop rather than hanging, and times out stuck calls.

## Notes

- Keep the browser open; the bridge drives whatever tab is active.
- Default port is `8777` (loopback only). Change it by passing a port to `bridge` / the daemon
  and editing `PORT` in `extension/background.js`.
- The extension only talks to `ws://127.0.0.1` — nothing leaves your machine.
