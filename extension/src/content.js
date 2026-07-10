// Content-script entrypoint: wires the hotkey/toolbar message to the panel.
// Load order (manifest): tickers.js -> intent.js -> panel.js -> content.js.

// Content scripts are injected once when a tab loads and then persist even
// after the extension is reloaded - only a page refresh picks up new code.
// This line makes that staleness checkable from the page's own DevTools
// console (F12 on the TradingView tab) without relying on spotting the
// panel's header text.
console.log(`[Enma] content script loaded on this tab - v${chrome.runtime.getManifest().version}`);

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "enma:toggle") EnmaPanel.toggle();
});
