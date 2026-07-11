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
//
// E1 (2026-07-11): strategies became per-host (TradingView + Kite web).
// Kite's DOM stubs below mirror structures read out of Kite web's own
// shipped bundle (kite-demo.zerodha.com main.*.js), see src/tickers.js.

const fs = require("fs");
const path = require("path");
const assert = require("assert");

function loadTickers(loc, doc) {
  global.location = loc;
  global.document = doc;
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "tickers.js"), "utf8");
  return eval(src + "; EnmaTickers");
}

const TV = "www.tradingview.com";
const KITE = "kite.zerodha.com";

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

// Minimal stub of Kite's marketwatch sidebar: one current `.instrument` row
// with a `.nice-name` and optional `.tags .tag` entries (exchange / INDEX).
function kiteDoc(row) {
  const rowEl = row && {
    querySelector(sel) {
      if (sel === ".nice-name") return { textContent: row.niceName };
      return null;
    },
    querySelectorAll(sel) {
      if (sel === ".tags .tag") return (row.tags || []).map((t) => ({ textContent: t }));
      return [];
    },
  };
  const sidebar = {
    querySelector(sel) {
      if (rowEl && (sel === ".instrument.selected" ||
                    (row.depthOnly && sel === ".instrument.active-marketdepth"))) {
        return row.depthOnly && sel === ".instrument.selected" ? null : rowEl;
      }
      return null;
    },
  };
  return {
    title: "Kite - Zerodha's fast and elegant flagship trading platform",
    querySelector(sel) {
      if (sel === ".marketwatch-sidebar") return sidebar;
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
  loadTickers({ hostname: TV, pathname: "/symbols/NASDAQ-SPCX/", search: "" }, { title: "SPCX" }).detect(),
  { symbol: "SPCX", exchange: "NASDAQ" }
);

check(
  "NYSE query param now resolves (US market model added)",
  loadTickers({ hostname: TV, pathname: "/chart/abc/", search: "?symbol=NYSE%3AAAPL" }, { title: "AAPL" }).detect(),
  { symbol: "AAPL", exchange: "NYSE" }
);

check(
  "a still-genuinely-unsupported exchange is confidently rejected, not fabricated",
  loadTickers({ hostname: TV, pathname: "/symbols/LSE-VOD/", search: "" }, { title: "VOD" }).detect(),
  { unsupported: true, exchange: "LSE" }
);

check(
  "a real hyphenated NSE ticker via the path strategy still resolves",
  loadTickers({ hostname: TV, pathname: "/symbols/NSE-BAJAJ-AUTO/", search: "" }, { title: "x" }).detect(),
  { symbol: "BAJAJ-AUTO", exchange: "NSE" }
);

check(
  "plain NSE query param still resolves",
  loadTickers({ hostname: TV, pathname: "/chart/x/", search: "?symbol=NSE%3ARELIANCE" }, { title: "x" }).detect(),
  { symbol: "RELIANCE", exchange: "NSE" }
);

check(
  "title-only fallback (no URL info) still resolves a hyphenated name",
  loadTickers({ hostname: TV, pathname: "/", search: "" }, { title: "BAJAJ-AUTO 2,850 +1.2%" }).detect(),
  { symbol: "BAJAJ-AUTO", exchange: "NSE" }
);

check(
  "BSE path is recognised",
  loadTickers({ hostname: TV, pathname: "/symbols/BSE-RELIANCE/", search: "" }, { title: "x" }).detect(),
  { symbol: "RELIANCE", exchange: "BSE" }
);

{
  const T = loadTickers({ hostname: TV, pathname: "/", search: "" }, { title: "x" });
  check("manual-entry normalise() unaffected", T.normalise("nse:infy"), { symbol: "INFY", exchange: "NSE" });
}

// --- Live-bug regression (2026-07-10): switching the active chart symbol
// via TradingView's own in-app picker left location.search reading a STALE
// "NSE:RELIANCE" while document.title (and the legend exchange badge)
// correctly tracked "INFY" - confirmed by driving a real TradingView chart.
// Enma kept reporting the old ticker no matter what was actually on screen.
{
  const staleLoc = { hostname: TV, pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "live legend beats a stale URL query param after an in-app symbol swap",
    loadTickers(staleLoc, docWithLegend("INFY 1,068.00 ▲ +1.64%", "NSE")).detect(),
    { symbol: "INFY", exchange: "NSE" }
  );
}

{
  // Legend also catches a genuinely unsupported exchange, live-synced -
  // titleStrategy alone could never know this, only the exchange badge can.
  const loc = { hostname: TV, pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "legend catches an unsupported exchange even when the URL still says NSE",
    loadTickers(loc, docWithLegend("VOD 75.20 ▲ +0.5%", "LSE")).detect(),
    { unsupported: true, exchange: "LSE" }
  );
}

{
  // And correctly resolves a live in-app swap TO a now-supported exchange.
  const loc = { hostname: TV, pathname: "/chart/", search: "?symbol=NSE%3ARELIANCE" };
  check(
    "legend resolves a live swap to NASDAQ correctly",
    loadTickers(loc, docWithLegend("AAPL 210.00 ▲ +0.5%", "NASDAQ")).detect(),
    { symbol: "AAPL", exchange: "NASDAQ" }
  );
}

{
  // On page types with no legend widget (e.g. /symbols/... static pages),
  // legendStrategy must no-op and defer to the URL strategy, not break it.
  const loc = { hostname: TV, pathname: "/symbols/NSE-RELIANCE/financials-overview/", search: "" };
  check(
    "no legend on screen -> falls through to the URL path strategy",
    loadTickers(loc, docWithLegend("x", null)).detect(),
    { symbol: "RELIANCE", exchange: "NSE" }
  );
}

// --- Kite (Zerodha) web, E1 -------------------------------------------------

check(
  "Kite chart window URL resolves (route: /chart/ext/ciq/:segment/:tradingsymbol/:token)",
  loadTickers({ hostname: KITE, pathname: "/chart/ext/ciq/NSE/INFY/408065", search: "" },
              kiteDoc(null)).detect(),
  { symbol: "INFY", exchange: "NSE" }
);

check(
  "Kite tvc/beta chart URL with an encoded ampersand ticker resolves",
  loadTickers({ hostname: KITE, pathname: "/chart/ext/tvc/beta/NSE/M%26M/519937", search: "" },
              kiteDoc(null)).detect(),
  { symbol: "M&M", exchange: "NSE" }
);

check(
  "Kite MCX chart is confidently unsupported, never guessed as NSE",
  loadTickers({ hostname: KITE, pathname: "/chart/ext/ciq/MCX/GOLDM25AUGFUT/121obscure", search: "" },
              kiteDoc(null)).detect(),
  { unsupported: true, exchange: "MCX" }
);

check(
  "Kite marketwatch selected row, no exchange tag -> NSE (Kite hides default-segment tags)",
  loadTickers({ hostname: KITE, pathname: "/marketwatch", search: "" },
              kiteDoc({ niceName: "RELIANCE" })).detect(),
  { symbol: "RELIANCE", exchange: "NSE" }
);

check(
  "Kite marketwatch row with a visible BSE tag resolves as BSE",
  loadTickers({ hostname: KITE, pathname: "/marketwatch", search: "" },
              kiteDoc({ niceName: "RELIANCE", tags: ["BSE"] })).detect(),
  { symbol: "RELIANCE", exchange: "BSE" }
);

check(
  "Kite index row (INDEX tag) is rejected - SENSEX must not become a fabricated NSE ticker",
  loadTickers({ hostname: KITE, pathname: "/marketwatch", search: "" },
              kiteDoc({ niceName: "SENSEX", tags: ["INDEX"] })).detect(),
  null
);

check(
  "Kite derivative row's spaced nice-name falls through to manual entry",
  loadTickers({ hostname: KITE, pathname: "/marketwatch", search: "" },
              kiteDoc({ niceName: "NIFTY 25JUL FUT" })).detect(),
  null
);

check(
  "Kite depth-pane-open row detected when nothing is keyboard-selected",
  loadTickers({ hostname: KITE, pathname: "/marketwatch", search: "" },
              kiteDoc({ niceName: "TCS", depthOnly: true })).detect(),
  { symbol: "TCS", exchange: "NSE" }
);

check(
  "Kite dashboard with nothing selected -> null (manual entry), never the site name as a ticker",
  loadTickers({ hostname: KITE, pathname: "/dashboard", search: "" }, kiteDoc(null)).detect(),
  null
);

check(
  "an unmatched host gets no strategies at all - manual entry only",
  loadTickers({ hostname: "groww.in", pathname: "/stocks/reliance-industries-ltd", search: "" },
              { title: "Reliance Industries Ltd Share Price" }).detect(),
  null
);

console.log(`\n${passed}/${passed} passed`);
