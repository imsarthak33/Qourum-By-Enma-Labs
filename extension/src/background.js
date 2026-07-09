// Enma service worker - the ONLY place that talks to the local bridge.
//
// MV3 detail that shapes the whole design: a content script's fetch carries
// the *page's* origin (https://tradingview.com), which `quorum serve`
// deliberately refuses (its CORS gate only trusts extension origins, so a
// random website can never drive the user's council). The service worker's
// origin is chrome-extension://<id> - exactly what the bridge allows - so all
// /analyze traffic flows: panel -> Port -> here -> SSE -> Port -> panel.
//
// Enma boundary (doc 08 par.4): this file computes nothing and rewrites
// nothing. It parses SSE frames and forwards the engine's events verbatim.

const BRIDGE = "http://127.0.0.1:8756";

// --- hotkey / toolbar toggle -> tell the active tab's panel ---------------
async function toggleActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "enma:toggle" });
  } catch {
    // No content script on this page (not a supported site) - nothing to do.
  }
}
chrome.commands.onCommand.addListener((cmd) => {
  if (cmd === "toggle-enma") toggleActiveTab();
});
chrome.action.onClicked.addListener(() => toggleActiveTab());

// --- SSE plumbing ----------------------------------------------------------
// Minimal SSE parser: accumulates text chunks, emits {event, data} per blank-
// line-delimited frame. Handles frames split across network chunks.
function makeSSEParser(onFrame) {
  let buf = "";
  return (chunk) => {
    buf += chunk;
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onFrame(event, data);
    }
  };
}

// One debate per Port. The panel opens a Port named "enma-analyze", sends
// {symbol, query, exchange}, and receives the engine's events verbatim.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "enma-analyze") return;
  const abort = new AbortController();
  port.onDisconnect.addListener(() => abort.abort()); // panel closed -> stop the stream

  port.onMessage.addListener(async (msg) => {
    const params = new URLSearchParams({ symbol: msg.symbol });
    if (msg.query) params.set("query", msg.query);
    if (msg.exchange) params.set("exchange", msg.exchange);

    try {
      const res = await fetch(`${BRIDGE}/analyze?${params}`, {
        signal: abort.signal,
        headers: { Accept: "text/event-stream" },
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        port.postMessage({
          type: "bridge-error",
          message: detail.error || `bridge answered ${res.status}`,
        });
        return;
      }

      const parse = makeSSEParser((event, data) => {
        let payload;
        try { payload = JSON.parse(data); } catch { return; }
        port.postMessage({ type: "event", event, payload }); // verbatim pass-through
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parse(decoder.decode(value, { stream: true }));
      }
      port.postMessage({ type: "stream-end" });
    } catch (err) {
      if (abort.signal.aborted) return;
      port.postMessage({
        type: "bridge-error",
        message:
          "Can't reach the local council bridge. Start it with:  quorum serve" +
          (err?.message ? `  (${err.message})` : ""),
      });
    }
  });
});
