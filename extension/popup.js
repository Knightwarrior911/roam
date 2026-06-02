// Roam Bridge popup: shows whether Roam is connected and which tabs it controls,
// and lets the user release those tabs or pause Roam entirely.
const $ = (id) => document.getElementById(id);

function render(s) {
  s = s || { connected: false, paused: false, controlledTabIds: [], count: 0 };
  const dot = $("dot");
  dot.className = "dot" + (s.paused ? " paused" : (s.connected ? " on" : ""));
  $("status").textContent = s.paused ? "Paused" : (s.connected ? "Connected to Roam" : "Waiting for Roam…");
  $("count").textContent = s.count || 0;

  const ul = $("tabs");
  ul.innerHTML = "";
  const ids = s.controlledTabIds || [];
  if (!ids.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = s.connected ? "No tabs under control yet." : "—";
    ul.appendChild(li);
  } else {
    // resolve tab titles for a friendlier list
    chrome.tabs.query({}, (tabs) => {
      const byId = new Map(tabs.map((t) => [t.id, t]));
      ul.innerHTML = "";
      for (const id of ids) {
        const t = byId.get(id);
        const li = document.createElement("li");
        li.textContent = t ? (t.title || t.url || ("tab " + id)) : ("tab " + id);
        ul.appendChild(li);
      }
    });
  }

  const toggle = $("toggle");
  toggle.textContent = s.paused ? "Resume" : "Pause";
  $("release").disabled = !(s.count > 0);
}

function refresh() {
  chrome.runtime.sendMessage({ type: "roam-get-state" }, (s) => {
    if (chrome.runtime.lastError) return;   // SW asleep; the next push will catch us up
    render(s);
  });
}

$("release").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "roam-release-all" }, () => refresh());
});
$("toggle").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "roam-get-state" }, (s) => {
    const type = (s && s.paused) ? "roam-resume" : "roam-pause";
    chrome.runtime.sendMessage({ type }, () => refresh());
  });
});

// live updates pushed by the service worker
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "roam-state") render(msg.state);
});

refresh();
