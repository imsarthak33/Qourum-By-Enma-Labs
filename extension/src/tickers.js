// Ticker detection - strategy list, first valid symbol wins (doc 00 P0 risk
// flag / doc 08 par.5). E0 ships TradingView only; Kite/Groww/Angel One are
// known-fragile follow-ups and natural first PRs (doc 00 par.A4).
//
// Every strategy returns a raw candidate string or null; the runner
// normalises ("NSE:RELIANCE" / "NSE-RELIANCE" -> {symbol, exchange}) and
// validates before accepting. On total failure the panel falls back to
// manual entry - detection failing must never block asking Enma.

const EnmaTickers = (() => {
  const VALID = /^[A-Z][A-Z0-9&-]{0,19}$/; // NSE/BSE symbol shape, e.g. RELIANCE, M&M, BAJAJ-AUTO
  const EXCHANGES = new Set(["NSE", "BSE"]);

  function normalise(raw) {
    if (!raw) return null;
    let s = decodeURIComponent(String(raw)).trim().toUpperCase();
    let exchange = "NSE";
    // "NSE:RELIANCE" (url param) or "NSE-RELIANCE" (path segment)
    const m = s.match(/^([A-Z]+)[:-](.+)$/);
    if (m && EXCHANGES.has(m[1])) {
      exchange = m[1];
      s = m[2];
    }
    return VALID.test(s) ? { symbol: s, exchange } : null;
  }

  // -- TradingView strategies, most reliable first ---------------------------
  function urlParamStrategy() {
    // https://www.tradingview.com/chart/XXXX/?symbol=NSE%3ARELIANCE
    return new URLSearchParams(location.search).get("symbol");
  }

  function urlPathStrategy() {
    // https://www.tradingview.com/symbols/NSE-RELIANCE/
    const m = location.pathname.match(/\/symbols\/([A-Z]+-[A-Z0-9&-]+)/i);
    return m ? m[1] : null;
  }

  function titleStrategy() {
    // Chart tabs title like: "RELIANCE 2,850.00 ▲ +1.2% ..." - first token.
    const first = (document.title || "").split(/[\s|]/)[0];
    return first && first.length >= 2 ? first : null;
  }

  function detect() {
    for (const strat of [urlParamStrategy, urlPathStrategy, titleStrategy]) {
      let candidate = null;
      try {
        candidate = normalise(strat());
      } catch {
        /* a broken strategy must never break detection */
      }
      if (candidate) return candidate;
    }
    return null; // panel shows manual entry
  }

  return { detect, normalise };
})();
