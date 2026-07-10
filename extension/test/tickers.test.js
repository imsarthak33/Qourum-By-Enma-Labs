// Plain-node regression test for ticker detection (no build step, no deps -
// matches the extension's zero-tooling install story). Run: node test/tickers.test.js
//
// Regression (live bug, 2026-07-10): visiting a page on an exchange Quorum
// couldn't yet serve produced a fabricated ticker sent straight to the
// backend instead of a clear "unsupported" signal. detect() must recognise a
// structurally-known unsupported exchange and stop, not guess. NASDAQ/NYSE
// were added to the supported set once the Macro Oracle got a matching US
// factor set (quorum/data/adapters.py's MACRO_TICKERS_BY_EXCHANGE) - the
// "unsupported" mechanism itself is still covered here using an exchange
// that remains genuinely out of scope (LSE).

const fs = require("fs");
const path = require("path");
const assert = require("assert");

function loadTickers(loc, doc) {
  global.location = loc;
  global.document = doc;
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "tickers.js"), "utf8");
  return eval(src + "; EnmaTickers");
}

// Minimal stub for a TradingView chart page's live legend widget, matching
// what a real page returns for `document.querySelector(...)`.
function docWithLegend(titleText, exchangeText) {
  return {
    title: titleText,
    querySelector(sel) {
      if (sel === '[data-qa-id="title-wrapper legend-source-exchange"]') {
        return exchangeText == null ? null : { textContent: exchangeText };
      }
      return null;
    },
  };
}

let passed = 0;
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected);
  passed++;
  console.log(`ok - ${name}`);
}

check(
  "NASDAQ path now resolves (US market model added)",
  loadTickers({ pathname: "/symbols/NASDAQ-SPCX/", search: "" }, { title: "SPCX" }).detect(),
  { symbol: "SPCX", exchange: "NASDAQ" }
);

check(
  "NYSE query param now resolves (US market model added)",
  loadTickers({ pathname: "/chart/abc/", search: "?symbol=NYSE%3AAAPL" }, { title: "AAPL" }).detect(),
  { symbol: "AAPL", exchange: "NYSE" }
);

check(
  "a still-genuinely-unsupported exchange is confidently rejected, not fabricated",
  loadTickers({ pathname: "/symbols/LSE-VOD/", search: "" }, { title: "VOD" }).detect(),
  { unsupported: true, exchange: "LSE" }
);

check(
  "a real hyphenated NSE ticker via the path strategy still resolves",
  loadTickers({ pathname: "/symbols/NSE-BAJAJ-AUTO/", search: "" }, { title: "x" }).detect(),
  { symbol: "BAJAJ-AUTO", exchange: "NSE" }
);

check(
  "plain NSE query param still resolves",
  loadTickers({ pathname: "/chart/x/", search: "?symbol=NSE%3ARELIANCE" }, { title: "x" }).detect(),
  { symbol: "RELIANCE", exchange: "NSE" }
);

check(
  "title-only fallback (no URL info) still resolves a hyphenated name",
  loadTickers({ pathname: "/", search: "" }, { title: "BAJAJ-AUTO 2,850 +1.2%" }).detect(),
  { symbol: "BAJAJ-AUTO", exchange: "NSE" }
);

check(
  "BSE path is recognised",
  loadTickers({ pathname: "/symbols/BSE-RELIANCE/", search: "" }, { title: "x" }).detect(),
  { symbol: "RELIANCE", exchange: "BSE" }
);

{
  const T = loadTickers({ pathname: "/", search: "" }, { title: "x" });
  check("manual-entry normalise() unaffected", T.normalise("nse:infy"), { symbol: "INFY", exchange: "NSE" });
}

// --- Live-bug regression (2026-07-10): switching the active chart symbol
// via TradingView's own in-app picker left location.search reading a STALE
// "NSE:RELIANCE" while document.title (and the legend exchange badge)
// correctly tracked "INFY" - confirmed by driving a real TradingView chart.
// Enma kept reporting the old ticker no matter what was actually on screen.
{
  const staleLoc = { pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "live legend beats a stale URL query param after an in-app symbol swap",
    loadTickers(staleLoc, docWithLegend("INFY 1,068.00 ▲ +1.64%", "NSE")).detect(),
    { symbol: "INFY", exchange: "NSE" }
  );
}

{
  // Legend also catches a genuinely unsupported exchange, live-synced -
  // titleStrategy alone could never know this, only the exchange badge can.
  const loc = { pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "legend catches an unsupported exchange even when the URL still says NSE",
    loadTickers(loc, docWithLegend("VOD 75.20 ▲ +0.5%", "LSE")).detect(),
    { unsupported: true, exchange: "LSE" }
  );
}

{
  // And correctly resolves a live in-app swap TO a now-supported exchange.
  const loc = { pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "legend resolves a live swap to NASDAQ correctly",
    loadTickers(loc, docWithLegend("AAPL 210.00 ▲ +0.5%", "NASDAQ")).detect(),
    { symbol: "AAPL", exchange: "NASDAQ" }
  );
}

{
  // On page types with no legend widget (e.g. /symbols/... static pages),
  // legendStrategy must no-op and defer to the URL strategy, not break it.
  const loc = { pathname: "/symbols/NSE-RELIANCE/financials-overview/", search: "" };
  check(
    "no legend on screen -> falls through to the URL path strategy",
    loadTickers(loc, docWithLegend("x", null)).detect(),
    { symbol: "RELIANCE", exchange: "NSE" }
  );
}

console.log(`\n${passed}/${passed} passed`);
