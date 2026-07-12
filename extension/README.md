# Enma — the Quorum council overlay

Ask Enma about the stock on your screen. Enma detects the ticker, convenes the
**Quorum council** running on *your* machine, and narrates the computed verdict
— live, in a panel on top of TradingView or Kite (Zerodha) web.

**Enma is the face; Quorum is the brain.** Every probability, level, weight,
and the verdict itself is computed by Quorum's deterministic Quant Core. Enma
never originates a number — it renders the engine's event stream verbatim.
There is no LLM inside the extension at all: Enma's "voice" is fixed template
text around numbers the engine computed.

Fully self-hosted: the extension only ever talks to `127.0.0.1`. Your keys,
your machine, your data. **AI analysis, not investment advice.**

## Install (2 minutes)

1. Start the local council bridge (from the repo root):

   ```bash
   pip install -e .
   export GROQ_API_KEY=gsk_...   # any one free key; optional — math runs without it
   quorum serve
   ```

2. Load the extension:
   - Chrome → `chrome://extensions` → enable **Developer mode**
   - **Load unpacked** → select this `extension/` folder

3. Open a chart on [tradingview.com](https://www.tradingview.com), or your
   marketwatch/chart on [kite.zerodha.com](https://kite.zerodha.com) — NSE,
   BSE, NASDAQ, and NYSE symbols all work (e.g. NSE:RELIANCE or NASDAQ:AAPL) —
   and press **Ctrl+Shift+Q** (or click the Enma toolbar icon).

4. Ask away — or just hit **Ask** for the full council read. New to a name?
   Paste a watchlist in the **Roast** box (`+RELIANCE NASDAQ:AAPL -TCS`) for an
   instant read on the whole set plus your "trading DNA" — `+` means you're
   long it, `-` short, neither just watching.

## Watch your portfolio (proactive alerts)

Type your holdings in the **Roast** box (`+RELIANCE NASDAQ:AAPL -TCS`) and hit
**Watch for changes**. Enma re-runs the council on the whole set on a schedule
(hourly) and fires a desktop notification **only when a verdict actually
changes** — `WAIT → BUY`, say — never on every tick. **Check now** forces an
immediate pass.

It's the same quant-only `/roast` the overlay uses, so it costs no inference.
Honest limitation, stated in the panel too: it can only check **while Chrome is
open and `quorum serve` is running** — a bridge-down check is skipped silently,
not queued. Nothing leaves your machine; the watched list lives in
`chrome.storage.local`.

## What you'll see

```
Enma: Hey — I can see RELIANCE on screen. Ask me anything about it.
you: should i buy this?
Enma: Let me put that to the council — the math will answer, I'll narrate.
  The Technician: computed P(bull) = 0.68
  The Fundamentalist: computed P(bull) = 0.61
  ...
  [council sentiment bar]
  The Chairman has computed the verdict:  WAIT
  P(bull) 0.5892 — EV 4.1 — edge 0.081 vs hurdle 0.15
  calibration confidence: low
  AI analysis, not investment advice.
```

## Platform support

| Platform | Ticker detection | Status |
|---|---|---|
| TradingView | live legend → URL param → URL path → title | ✅ E0 |
| Kite (Zerodha) | chart-window URL → selected marketwatch row | ✅ E1 |
| Groww | — | PR welcome |
| Angel One | — | PR welcome |

On Kite, Enma reads the **selected** marketwatch row (or the one with the
depth pane open) and any chart window. Indices and derivatives are declined
honestly — the council debates equities. Detection strategies are strictly
per-host: an unrecognised page never guesses, it offers manual entry.

Detection is a strategy list (`src/tickers.js`) — first valid symbol on a
**supported exchange** (`NSE`, `BSE`, `NASDAQ`, `NYSE`) wins, manual entry as
the fallback. A recognised-but-unsupported exchange (e.g. LSE) is flagged
plainly rather than guessed. Adding a platform = one small strategy file + a
manifest match pattern. Great first contribution.

**US-market support isn't a thin ticker unblock** — the Macro Oracle regresses
against a market-specific factor set (S&P 500 / DXY / WTI for NASDAQ/NYSE vs.
NIFTY / USDINR / Brent for NSE/BSE, see `quorum/data/adapters.py`'s
`MACRO_TICKERS_BY_EXCHANGE`), so a NASDAQ verdict is computed against the
right market, not silently against India's.

## Architecture (why the service worker does the fetching)

```
panel (Shadow DOM, page) ──Port──► service worker ──fetch──► quorum serve (127.0.0.1)
                                       │  "enma-analyze" → GET /analyze  (SSE, streamed)
                                       └  "enma-roast"   → GET /roast    (one JSON reply)
```

The roast is quant-only (no LLM narration) and computed in one shot, so it's a
single JSON reply rather than a stream; a full debate streams the council's
progress as it resolves.

`quorum serve`'s CORS gate only trusts `chrome-extension://` origins, so a
random web page can never drive your council or burn your API quota. Content
scripts fetch with the *page's* origin, so all bridge traffic goes through the
service worker — whose origin is the extension's — and events are forwarded to
the panel verbatim.

## Troubleshooting

- **"Can't reach the local council bridge"** → `quorum serve` isn't running,
  or it's on a non-default port (default `8756`; the port is set in
  `src/background.js` as `BRIDGE`).
- **"I couldn't read a ticker from this page"** → use the manual ticker box —
  detection strategies never block asking.
- Hotkey conflict? Rebind at `chrome://extensions/shortcuts`.
