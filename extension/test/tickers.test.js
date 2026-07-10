// Plain-node regression test for ticker detection (no build step, no deps -
// matches the extension's zero-tooling install story). Run: node test/tickers.test.js
//
// Regression (live bug, 2026-07-10): visiting a NASDAQ page produced
// {symbol:"NASDAQ-SPCX", exchange:"NSE"} - a fabricated ticker sent straight
// to the backend, which Quorum can't serve (NSE/BSE only) and which
// contributed to a silent "frozen" panel. detect() must recognise a
// structurally-known non-NSE/BSE exchange and stop, not guess.

const fs = require("fs");
const path = require("path");
const assert = require("assert");

function loadTickers(loc, doc) {
  global.location = loc;
  global.document = doc;
  const src = fs.readFileSync(path.join(__dirname, "..", "src", "tickers.js"), "utf8");
  return eval(src + "; EnmaTickers");
}

let passed = 0;
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected);
  passed++;
  console.log(`ok - ${name}`);
}

check(
  "NASDAQ path is confidently rejected, not fabricated as NSE",
  loadTickers({ pathname: "/symbols/NASDAQ-SPCX/", search: "" }, { title: "SPCX" }).detect(),
  { unsupported: true, exchange: "NASDAQ" }
);

check(
  "NYSE query param is confidently rejected",
  loadTickers({ pathname: "/chart/abc/", search: "?symbol=NYSE%3AAAPL" }, { title: "AAPL" }).detect(),
  { unsupported: true, exchange: "NYSE" }
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

console.log(`\n${passed}/${passed} passed`);
