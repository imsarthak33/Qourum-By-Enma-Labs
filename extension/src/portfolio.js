// Proactive portfolio alerts - the pure diff logic (growth plan Horizon 2).
// Kept dependency-free (no chrome.* APIs) so it can be unit-tested in plain
// node, exactly like tickers.js. The service worker importScripts() this and
// wraps it with the chrome.alarms / notifications / storage plumbing.
//
// Enma boundary (doc 08 par.4): this decides only WHETHER an action changed;
// the actions themselves come verbatim from the /roast payload (which comes
// from the Chairman). Nothing here originates or adjusts a number.

const EnmaPortfolio = (() => {
  const keyOf = (r) => `${r.exchange}:${r.symbol}`;

  // Changes worth speaking about: an ok read whose action DIFFERS from a
  // previously-known action for the same instrument. A symbol with no prior
  // record (first time seen) is a baseline, not a change - it never alerts.
  function diffActions(prev, reads) {
    const before = prev || {};
    const changes = [];
    for (const r of reads || []) {
      if (!r.ok || !r.action) continue;
      const k = keyOf(r);
      if (before[k] !== undefined && before[k] !== r.action) {
        changes.push({ key: k, symbol: r.symbol, exchange: r.exchange,
                       from: before[k], to: r.action });
      }
    }
    return changes;
  }

  // The updated last-known map: prior knowledge, overwritten by every ok read
  // (a failed read leaves the last good action in place rather than forgetting
  // it on a transient data hiccup).
  function nextKnown(prev, reads) {
    const next = { ...(prev || {}) };
    for (const r of reads || []) if (r.ok && r.action) next[keyOf(r)] = r.action;
    return next;
  }

  return { diffActions, nextKnown };
})();

// Node test harness reads this via eval, same pattern as tickers.js.
if (typeof module !== "undefined" && module.exports) module.exports = EnmaPortfolio;
