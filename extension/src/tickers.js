// Ticker detection - strategy list, first valid symbol wins (doc 00 P0 risk
// flag / doc 08 par.5). E0 ships TradingView only; Kite/Groww/Angel One are
// known-fragile follow-ups and natural first PRs (doc 00 par.A4).
//
// Quorum only covers NSE/BSE (quorum/data/adapters.py's YF_SUFFIX has no
// entry for anything else) - so a strategy that structurally KNOWS the
// exchange (the URL always encodes it, TradingView-side) must say so
// definitively rather than silently falling through to a lower-confidence
// strategy that has no exchange info and would just re-guess NSE. A real bug:
// visiting a NASDAQ page produced {symbol:"NASDAQ-SPCX", exchange:"NSE"} -
// a fabricated ticker sent straight to the backend. Detection now returns
// one of three shapes: {symbol, exchange} (usable), null (try the next
// strategy / fall back to manual entry), or {unsupported, exchange} (we
// KNOW the exchange and it isn't NSE/BSE - stop guessing immediately).

const EnmaTickers = (() => {
  const VALID = /^[A-Z][A-Z0-9&-]{0,19}$/; // NSE/BSE symbol shape, e.g. RELIANCE, M&M, BAJAJ-AUTO
  const EXCHANGES = new Set(["NSE", "BSE"]);

  function validSymbol(s) {
    return VALID.test(s) ? s : null;
  }

  // The URL strategies parse TradingView's own EXCHANGE:SYMBOL / EXCHANGE-
  // SYMBOL convention, so the prefix is structurally guaranteed to BE the
  // exchange (never part of the ticker name) - an unrecognised one here is a
  // confident "out of scope", not a guess.
  function fromExchangePair(exchangeRaw, symbolRaw) {
    const exchange = String(exchangeRaw || "").trim().toUpperCase();
    const symbol = validSymbol(decodeURIComponent(String(symbolRaw || "")).trim().toUpperCase());
    if (!symbol) return null; // malformed - let the next strategy try
    if (!EXCHANGES.has(exchange)) return { unsupported: true, exchange };
    return { symbol, exchange };
  }

  // For a bare token with NO structural exchange marker (manual entry, the
  // title fallback). A leading "NSE:"/"NSE-" is honoured; anything else is
  // assumed to be an NSE symbol whose own name may contain a hyphen
  // (BAJAJ-AUTO, M&M) - there's no way to tell "unknown exchange prefix"
  // from "hyphenated ticker name" from a bare string, so this defaults to
  // NSE rather than reject. That ambiguity is exactly why the URL-based
  // strategies (which DO know) run first and take priority.
  function normalise(raw) {
    if (!raw) return null;
    let s = decodeURIComponent(String(raw)).trim().toUpperCase();
    let exchange = "NSE";
    const m = s.match(/^([A-Z]+)[:-](.+)$/);
    if (m && EXCHANGES.has(m[1])) {
      exchange = m[1];
      s = m[2];
    }
    const symbol = validSymbol(s);
    return symbol ? { symbol, exchange } : null;
  }

  // -- TradingView strategies, most reliable first ---------------------------
  function urlParamStrategy() {
    // https://www.tradingview.com/chart/XXXX/?symbol=NSE%3ARELIANCE
    const raw = new URLSearchParams(location.search).get("symbol");
    if (!raw) return null;
    const decoded = decodeURIComponent(raw).toUpperCase();
    const m = decoded.match(/^([A-Z]+):(.+)$/); // TradingView's query param always uses ':'
    return m ? fromExchangePair(m[1], m[2]) : normalise(decoded);
  }

  function urlPathStrategy() {
    // https://www.tradingview.com/symbols/NSE-RELIANCE/  or  .../NASDAQ-SPCX/
    // TradingView's path convention is always EXCHANGE-SYMBOL, so the first
    // segment is definitively the exchange, not part of the ticker name.
    const m = location.pathname.match(/\/symbols\/([A-Z]+)-([A-Z0-9&-]+)/i);
    return m ? fromExchangePair(m[1], m[2]) : null;
  }

  function titleStrategy() {
    // Chart tabs title like: "RELIANCE 2,850.00 ▲ +1.2% ..." - first token.
    // No exchange info here, so this can only ever produce an NSE guess or
    // nothing - it never returns {unsupported}.
    const first = (document.title || "").split(/[\s|]/)[0];
    return first && first.length >= 2 ? normalise(first) : null;
  }

  function detect() {
    for (const strat of [urlParamStrategy, urlPathStrategy, titleStrategy]) {
      let candidate = null;
      try {
        candidate = strat();
      } catch {
        /* a broken strategy must never break detection */
      }
      if (!candidate) continue;
      // A confident "this is NASDAQ/NYSE/etc" stops the search outright -
      // falling through to titleStrategy would just reintroduce the same
      // bug (guessing NSE) from a source with even less information.
      return candidate;
    }
    return null; // panel shows manual entry
  }

  return { detect, normalise };
})();
