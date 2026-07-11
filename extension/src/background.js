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

// Stale-code confusion has cost real debugging time twice now: the extension
// tile and any already-open tab each cache their own copy of this code until
// explicitly reloaded/refreshed, with no visible sign that's happened. This
// line is the fastest way to check "is this actually the new build?" -
// chrome://extensions -> Enma -> "service worker" (inspect) -> Console.
console.log(`[Enma] service worker loaded - v${chrome.runtime.getManifest().version}`);

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

    // Live symptom this guards against: a hung/very-slow request left the
    // panel showing "busy" forever with no error - build_fact_pack does
    // several SEQUENTIAL yfinance calls (ohlcv, fundamentals, 3x macro
    // factors) before the engine emits its next event, so a slow network or
    // a bad symbol can go quiet for a while with zero intermediate progress.
    // This is an IDLE timeout (resets on every byte received, including
    // response headers), not a flat cap, so a legitimately slow-but-working
    // debate is never cut off mid-stream - only true silence trips it.
    const IDLE_TIMEOUT_MS = 45000;
    let idleTimer;
    let timedOut = false;
    const armIdleTimer = () => {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => { timedOut = true; abort.abort(); }, IDLE_TIMEOUT_MS);
    };

    try {
      armIdleTimer();
      const res = await fetch(`${BRIDGE}/analyze?${params}`, {
        signal: abort.signal,
        headers: { Accept: "text/event-stream" },
      });
      armIdleTimer(); // headers arrived; reset the clock for the body
      if (!res.ok) {
        clearTimeout(idleTimer);
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
        armIdleTimer(); // any byte is forward progress; push the deadline out
        parse(decoder.decode(value, { stream: true }));
      }
      clearTimeout(idleTimer);
      port.postMessage({ type: "stream-end" });
    } catch (err) {
      clearTimeout(idleTimer);
      if (abort.signal.aborted && !timedOut) return; // panel closed intentionally
      if (timedOut) {
        port.postMessage({
          type: "bridge-error",
          message: `The council went quiet for ${IDLE_TIMEOUT_MS / 1000}s+ and I stopped waiting - `
            + "the symbol may be invalid or a data source is slow. Check the quorum serve "
            + "terminal, or try again.",
        });
        return;
      }
      port.postMessage({
        type: "bridge-error",
        message:
          "Can't reach the local council bridge. Start it with:  quorum serve" +
          (err?.message ? `  (${err.message})` : ""),
      });
    }
  });
});

// One watchlist roast per Port. The panel opens "enma-roast" and sends
// {symbols: "+RELIANCE,NASDAQ:AAPL,-TCS"}; the bridge replies with one JSON
// body (roast is quant-only and computed in a single shot, not streamed).
// Same reason as /analyze the service worker does the fetch: a content-script
// fetch carries the page origin, which quorum serve's CORS gate refuses.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "enma-roast") return;
  const abort = new AbortController();
  port.onDisconnect.addListener(() => abort.abort());

  port.onMessage.addListener(async (msg) => {
    // A roast fetches one fact pack per name (concurrently, server-side); a big
    // watchlist can legitimately take a while, so the cap is generous. It's a
    // flat timeout, not idle - a single JSON reply has no intermediate bytes.
    const timer = setTimeout(() => abort.abort(), 90000);
    // encodeURIComponent so the +/- side markers survive - a bare + in a query
    // string decodes to a space and the long/short marker would be lost.
    const url = `${BRIDGE}/roast?symbols=${encodeURIComponent(msg.symbols || "")}`;
    try {
      const res = await fetch(url, {
        signal: abort.signal,
        headers: { Accept: "application/json" },
      });
      clearTimeout(timer);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        port.postMessage({ type: "bridge-error",
                           message: body.error || `bridge answered ${res.status}` });
        return;
      }
      port.postMessage({ type: "roast-result", payload: body });
    } catch (err) {
      clearTimeout(timer);
      if (abort.signal.aborted && err?.name === "AbortError") {
        port.postMessage({ type: "bridge-error",
                           message: "The roast took too long and I stopped waiting - "
                             + "try fewer names, or check the quorum serve terminal." });
        return;
      }
      port.postMessage({ type: "bridge-error",
                         message: "Can't reach the local council bridge. Start it with:  "
                           + "quorum serve" + (err?.message ? `  (${err.message})` : "") });
    }
  });
});
