// Plain-node test for the proactive-alerts diff logic (no build, no deps -
// same harness style as tickers.test.js). Run: node test/portfolio.test.js

const fs = require("fs");
const path = require("path");
const assert = require("assert");

const src = fs.readFileSync(path.join(__dirname, "..", "src", "portfolio.js"), "utf8");
const EnmaPortfolio = eval(src + "; EnmaPortfolio");

let passed = 0;
function check(name, actual, expected) {
  assert.deepStrictEqual(actual, expected);
  passed++;
  console.log(`ok - ${name}`);
}

const reads = [
  { exchange: "NSE", symbol: "RELIANCE", ok: true, action: "BUY" },
  { exchange: "NASDAQ", symbol: "AAPL", ok: true, action: "WAIT" },
];

check(
  "first sighting is a baseline, not a change (no prior record -> no alert)",
  EnmaPortfolio.diffActions({}, reads),
  []
);

check(
  "a changed action for a known instrument is reported with from/to",
  EnmaPortfolio.diffActions({ "NSE:RELIANCE": "WAIT", "NASDAQ:AAPL": "WAIT" }, reads),
  [{ key: "NSE:RELIANCE", symbol: "RELIANCE", exchange: "NSE", from: "WAIT", to: "BUY" }]
);

check(
  "an unchanged action for a known instrument is silent",
  EnmaPortfolio.diffActions({ "NSE:RELIANCE": "BUY", "NASDAQ:AAPL": "WAIT" }, reads),
  []
);

check(
  "a failed read never alerts and never overwrites the last good action",
  EnmaPortfolio.diffActions(
    { "NSE:INFY": "BUY" },
    [{ exchange: "NSE", symbol: "INFY", ok: false, error: "no price data" }]
  ),
  []
);

check(
  "nextKnown seeds from ok reads",
  EnmaPortfolio.nextKnown({}, reads),
  { "NSE:RELIANCE": "BUY", "NASDAQ:AAPL": "WAIT" }
);

check(
  "nextKnown keeps a prior action when the latest read failed (no forgetting on a hiccup)",
  EnmaPortfolio.nextKnown(
    { "NSE:INFY": "BUY" },
    [{ exchange: "NSE", symbol: "INFY", ok: false, error: "timeout" }]
  ),
  { "NSE:INFY": "BUY" }
);

console.log(`\n${passed}/${passed} passed`);
