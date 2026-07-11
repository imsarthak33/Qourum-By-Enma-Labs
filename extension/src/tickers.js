// Ticker detection - per-host strategy list, first valid symbol wins (doc 00
// P0 risk flag / doc 08 par.5). E1: TradingView + Kite (Zerodha) web.
// Groww/Angel One remain natural first PRs (doc 00 par.A4) - copy the Kite
// pattern: ground every selector in the site's real DOM/bundle, never guess.
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
  const VALID = /^[A-Z][A-Z0-9&-]{0,19}$/; // NSE/BSE/NASDAQ/NYSE symbol shape, e.g. RELIANCE, M&M, AAPL
  // Exchanges Quorum can actually run a correct debate for - the Macro
  // Oracle needs a matching factor set (quorum/data/adapters.py's
  // MACRO_TICKERS_BY_EXCHANGE), not just a price feed. Anything else is
  // still confidently flagged {unsupported} rather than guessed.
  const EXCHANGES = new Set(["NSE", "BSE", "NASDAQ", "NYSE"]);

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

  // Live bug (confirmed 2026-07-10 by driving an actual TradingView chart):
  // on /chart/ pages, `?symbol=` is only a deep-link hint set at INITIAL
  // load. Swapping the active symbol via TradingView's own in-app picker
  // (exactly what a user does while Enma stays open) does NOT update it -
  // switching RELIANCE -> INFY left location.search reading stale
  // "NSE:RELIANCE" while the page's own legend widget updated correctly.
  // That's why Enma kept reporting the old ticker no matter what was
  // actually on screen.
  //
  // TradingView's chart legend carries the live symbol via a stable,
  // semantically-named `data-qa-id="title-wrapper legend-source-exchange"`
  // badge (a QA automation hook - far less likely to be renamed on a routine
  // visual refactor than the hashed CSS module classnames nearby), and
  // `document.title`'s first token is empirically the live ticker code
  // itself (not the company name), confirmed to update in lockstep with the
  // legend. Together they give a fully live-synced {symbol, exchange} - and
  // still catch a genuinely unsupported exchange via fromExchangePair,
  // unlike title alone. On page types without this legend (e.g. the
  // /symbols/... static overview pages) the selector simply finds nothing
  // and this strategy no-ops, deferring to the URL strategies below, which
  // ARE reliable there (a full navigation happens per symbol on those pages).
  function legendStrategy() {
    const exchangeEl = document.querySelector('[data-qa-id="title-wrapper legend-source-exchange"]');
    const exchange = exchangeEl?.textContent?.trim();
    const tickerFromTitle = (document.title || "").split(/[\s|]/)[0];
    if (!exchange || !tickerFromTitle) return null;
    return fromExchangePair(exchange, tickerFromTitle);
  }

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

  // -- Kite (Zerodha) strategies ----------------------------------------------
  //
  // Every selector here is verified against Kite web's own shipped bundle
  // (the public kite-demo.zerodha.com instance's main.*.js Vue templates and
  // router constants, inspected 2026-07-11), not guessed from memory:
  //
  //  - Chart windows are the route "/chart/ext/ciq/:segment/:tradingsymbol/
  //    :token" (a tvc variant and /beta children exist alongside) - the
  //    segment slot is structurally the exchange, same guarantee as
  //    TradingView's URL conventions.
  //  - Marketwatch rows are `.instrument` items; the current one carries
  //    `.selected` (Kite's own keyboard/click position) or
  //    `.active-marketdepth` (depth pane expanded). The symbol lives in
  //    `span.nice-name`; the exchange renders as a sibling `.tags .tag` ONLY
  //    when it isn't one of Kite's defaults (showExchange() hides
  //    NSE/NFO/CDS/INDICES) - so "no tag" means NSE for anything shaped like
  //    an equity symbol. Non-tradable rows always carry an INDEX tag and are
  //    rejected here explicitly: "SENSEX" would otherwise pass the symbol
  //    regex and fabricate an NSE:SENSEX debate. Derivatives' spaced nice
  //    names ("NIFTY 25JUL FUT") fail the symbol shape on their own and fall
  //    through to manual entry.

  function kiteChartStrategy() {
    const m = location.pathname.match(
      /^\/chart\/ext\/[a-z]+(?:\/beta)?\/([A-Za-z]+)\/([^/]+)\/\d+/
    );
    return m ? fromExchangePair(m[1], m[2]) : null;
  }

  function kiteWatchStrategy() {
    const sidebar = document.querySelector(".marketwatch-sidebar");
    if (!sidebar) return null;
    const row = sidebar.querySelector(".instrument.selected")
      || sidebar.querySelector(".instrument.active-marketdepth");
    if (!row) return null;
    const name = row.querySelector(".nice-name")?.textContent?.trim();
    if (!name) return null;
    const tags = Array.from(row.querySelectorAll(".tags .tag"),
                            (t) => (t.textContent || "").trim().toUpperCase());
    if (tags.includes("INDEX")) return null; // indices aren't debatable tickers
    const exchange = tags.find((t) => t && t !== "EVENT");
    return fromExchangePair(exchange || "NSE", name);
  }

  // -- host routing ------------------------------------------------------------
  // Strategies are strictly per-host: TradingView's title fallback running on
  // Kite would read the site's own name ("Kite - Zerodha's...") as an NSE
  // ticker KITE - the same fabrication bug class the {unsupported} shape
  // exists to prevent. An unmatched host gets NO strategies and lands on
  // manual entry, never a guess.
  const HOST_STRATEGIES = [
    [/(^|\.)tradingview\.com$/, [legendStrategy, urlParamStrategy, urlPathStrategy, titleStrategy]],
    [/^kite(-demo)?\.zerodha\.com$/, [kiteChartStrategy, kiteWatchStrategy]],
  ];

  function detect() {
    const host = String(location.hostname || "");
    const entry = HOST_STRATEGIES.find(([re]) => re.test(host));
    for (const strat of entry ? entry[1] : []) {
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
