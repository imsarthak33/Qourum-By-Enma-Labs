# Enma — the Quorum council overlay

Ask Enma about the stock on your screen. Enma detects the ticker, convenes the
**Quorum council** running on *your* machine, and narrates the computed verdict
— live, in a panel on top of TradingView.

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

3. Open a chart on [tradingview.com](https://www.tradingview.com) (e.g. NSE:RELIANCE)
   and press **Ctrl+Shift+Q** (or click the Enma toolbar icon).

4. Ask away — or just hit **Ask** for the full council read.

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
| TradingView | URL param → URL path → title | ✅ E0 |
| Kite (Zerodha) | — | PR welcome |
| Groww | — | PR welcome |
| Angel One | — | PR welcome |

Detection is a strategy list (`src/tickers.js`) — first valid NSE/BSE symbol
wins, manual entry as the fallback. Adding a platform = one small strategy
file + a manifest match pattern. Great first contribution.

## Architecture (why the service worker does the fetching)

```
panel (Shadow DOM, page) ──Port──► service worker ──fetch/SSE──► quorum serve (127.0.0.1)
```

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
