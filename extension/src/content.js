// Content-script entrypoint: wires the hotkey/toolbar message to the panel.
// Load order (manifest): tickers.js -> panel.js -> content.js (shared scope).

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "enma:toggle") EnmaPanel.toggle();
});
