// The Enma panel - a Shadow-DOM overlay that renders the council's SSE stream.
//
// Enma boundary (doc 08 par.4, golden rule): every number shown here is read
// VERBATIM from an engine event payload. Enma's own voice is limited to fixed
// template strings around those numbers ("Let me convene the council...") -
// it never computes, adjusts, rounds, or invents a value. In E0 there is no
// LLM inside the extension at all; the conversational texture comes from the
// engine's own narrations plus these templates.
//
// E1 addition: a keyword intent classifier (intent.js) picks which agent's
// already-computed facts answer the question asked. This is still "select +
// phrase, never originate" - the direct-answer card below is built only from
// fields already present in feature_ready/agent_done/chairman payloads.

const EnmaPanel = (() => {
  const AGENT_LABELS = {
    technician: "The Technician",
    fundamentalist: "The Fundamentalist",
    macro: "The Macro Oracle",
    devils_advocate: "The Devil's Advocate",
    risk: "The Risk Ranger",
  };
  const ACTION_COLORS = {
    BUY: "#22c55e", SELL: "#ef4444", WAIT: "#eab308",
    AVOID: "#ef4444", NO_CALL: "#38bdf8",
  };
  const STANCE_COLORS = { BULL: "#22c55e", BEAR: "#ef4444", NEUTRAL: "#eab308" };

  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; margin: 0; }
    .wrap {
      position: fixed; top: 16px; right: 16px; z-index: 2147483647;
      width: 400px; max-height: calc(100vh - 32px);
      display: flex; flex-direction: column;
      background: #0b1020; color: #e2e8f0;
      border: 1px solid #263048; border-radius: 14px;
      box-shadow: 0 16px 48px rgba(0,0,0,.55);
      font: 13px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif;
      overflow: hidden;
    }
    header {
      display: flex; align-items: center; gap: 8px;
      padding: 12px 14px; background: #0f172a; border-bottom: 1px solid #263048;
      cursor: move; user-select: none;
    }
    .orb {
      width: 10px; height: 10px; border-radius: 50%;
      background: radial-gradient(circle at 35% 35%, #a78bfa, #6d28d9);
      box-shadow: 0 0 8px #7c3aed;
    }
    .orb.busy { animation: pulse 1.1s ease-in-out infinite; }
    @keyframes pulse { 50% { transform: scale(1.35); opacity: .7; } }
    header b { font-size: 14px; }
    header .sub { color: #64748b; font-size: 11px; }
    .chip {
      margin-left: auto; padding: 2px 10px; border-radius: 999px;
      background: #1e293b; border: 1px solid #334155;
      color: #93c5fd; font-weight: 600; font-size: 12px;
    }
    .close {
      background: none; border: none; color: #64748b;
      font-size: 16px; cursor: pointer; padding: 0 2px;
    }
    .close:hover { color: #e2e8f0; }
    .feed { flex: 1; overflow-y: auto; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px; }
    .enma { color: #c4b5fd; }
    .dim { color: #64748b; font-size: 12px; }
    .warn { color: #fbbf24; font-size: 12px; }
    .err { color: #f87171; }
    .feature b { color: #f1f5f9; }
    .agent {
      background: #111a2e; border: 1px solid #1f2a44;
      border-radius: 10px; padding: 8px 10px;
    }
    .agent.direct-answer { background: #1a1233; }
    .agent.direct-answer .head b { color: #c4b5fd; }
    .agent .head { display: flex; gap: 8px; align-items: baseline; }
    .agent .head b { font-size: 12.5px; }
    .stance { font-weight: 700; font-size: 11px; letter-spacing: .04em; }
    .agent .body { color: #cbd5e1; margin-top: 4px; font-size: 12.5px; }
    .bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: #1e293b; }
    .bar .bull { background: #22c55e; }
    .bar .bear { background: #ef4444; }
    .barlabel { font-size: 11px; color: #94a3b8; margin-top: 3px; }
    .verdict { border-radius: 12px; padding: 12px; border: 1px solid; background: #0f172a; }
    .verdict .action { font-size: 20px; font-weight: 800; letter-spacing: .06em; }
    .verdict .nums { margin-top: 6px; font-size: 12.5px; color: #cbd5e1; }
    .verdict .nums b { color: #f8fafc; }
    .verdict .rationale { margin-top: 8px; color: #e2e8f0; font-size: 12.5px; }
    .verdict .meta { margin-top: 8px; font-size: 11px; color: #64748b; }
    .disclaimer { font-size: 11px; color: #64748b; font-style: italic; margin-top: 6px; }
    footer { padding: 10px 12px; border-top: 1px solid #263048; background: #0f172a; }
    .row { display: flex; gap: 8px; }
    input {
      flex: 1; background: #0b1020; color: #e2e8f0;
      border: 1px solid #334155; border-radius: 9px; padding: 8px 10px;
      font: inherit; outline: none;
    }
    input:focus { border-color: #7c3aed; }
    input::placeholder { color: #475569; }
    button.ask {
      background: linear-gradient(135deg, #7c3aed, #6d28d9); color: #fff;
      border: none; border-radius: 9px; padding: 8px 14px;
      font: inherit; font-weight: 700; cursor: pointer;
    }
    button.ask:disabled { opacity: .5; cursor: default; }
    .symrow { margin-top: 8px; display: none; }
    .symrow.show { display: flex; }
  `;

  let root, els, busy = false;
  // Per-run state for the E1 direct-answer feature: which agent facts have
  // arrived so far, and what the current question was classified as.
  let session = {};
  let currentIntent = { category: "general" };

  // Live bug: typing into Enma's input on a TradingView CHART page (not the
  // overview/detail pages) leaked every keystroke into TradingView's own
  // "type anywhere to jump to symbol search" overlay. TradingView almost
  // certainly registers that as a CAPTURE-phase keydown listener on
  // `document` - capture listeners on an ancestor fire BEFORE the event
  // reaches our shadow-DOM input, so the per-input e.stopPropagation() calls
  // below (bubble phase) run structurally too late to stop it; by the time
  // our input sees the keystroke, TradingView's document-capture handler has
  // already acted on it.
  //
  // `window` is the earliest possible point in the capture phase - a capture
  // listener registered there always runs before one on `document`,
  // regardless of which script loaded first. `root` (the shadow host) is a
  // normal light-DOM node, so a listener outside a CLOSED shadow root still
  // sees `event.target` correctly retargeted to it when the keystroke
  // originated inside - no need to reach into the shadow tree at all.
  function isEnmaEvent(e) {
    if (!root) return false;
    if (e.target === root) return true;
    return typeof e.composedPath === "function" && e.composedPath().includes(root);
  }
  for (const type of ["keydown", "keypress", "keyup"]) {
    window.addEventListener(type, (e) => { if (isEnmaEvent(e)) e.stopPropagation(); }, true);
  }

  // Each builder returns a string made ONLY from fields already present in
  // session[agent] (populated verbatim from feature_ready/agent_done) or the
  // verdict payload - selection + phrasing, never a new number (doc 08 par.4).
  const DIRECT_ANSWER_BUILDERS = {
    technical: (s) => {
      const t = s.technician;
      if (!t) return null;
      const f = t.features || {};
      const bits = [];
      if (f.regime) bits.push(`regime **${f.regime}**`);
      if (f.z_tech != null) bits.push(`z-score ${f.z_tech}`);
      if (f.volume_z != null) bits.push(`volume z ${f.volume_z}`);
      const lead = bits.length ? `The Technician's read: ${bits.join(", ")}. ` : "";
      return (lead + (t.reasoning || "")).trim() || null;
    },
    risk: (s) => {
      const r = s.risk;
      if (!r) return null;
      const f = r.features || {};
      const lead = (f.entry != null && f.stop != null)
        ? `Entry ${f.entry}, stop ${f.stop}${f.atr_14 != null ? `, ATR(14) ${f.atr_14}` : ""}. `
        : "";
      return (lead + (r.reasoning || "")).trim() || null;
    },
    valuation: (s) => {
      const f = s.fundamentalist;
      if (!f) return null;
      const lead = f.p_bull != null ? `Fundamentalist P(bull) ${f.p_bull}. ` : "";
      return (lead + (f.reasoning || "")).trim() || null;
    },
    macro: (s) => {
      const m = s.macro;
      if (!m) return null;
      const lead = m.p_bull != null ? `Macro Oracle P(bull) ${m.p_bull}. ` : "";
      return (lead + (m.reasoning || "")).trim() || null;
    },
    // "verdict" intent has no separate card: the Chairman card that always
    // renders IS the direct answer to "should I buy?" - no duplication.
  };

  function h(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text != null) el.textContent = text;
    return el;
  }

  function line(cls, text) {
    const el = h("div", cls, text);
    els.feed.appendChild(el);
    els.feed.scrollTop = els.feed.scrollHeight;
    return el;
  }

  // ---- event rendering: payload numbers pass through untouched -------------
  function renderEvent(event, p) {
    switch (event) {
      case "debate_start":
        line("enma", `Convening the council on ${p.symbol}...`);
        break;
      case "fact_pack": {
        const missing = Object.entries(p.sources || {})
          .filter(([, v]) => v !== "ok").map(([k]) => k);
        line("dim", missing.length
          ? `fact pack ready (missing: ${missing.join(", ")})`
          : "fact pack ready");
        break;
      }
      case "feature_ready": {
        const name = AGENT_LABELS[p.agent] || p.agent;
        if (p.triggered === false) {
          const D = p.features?.D;
          line("dim", `${name}: divergence test did not fire${D != null ? ` (D=${D})` : ""} - silent this round`);
        } else if (p.p_bull != null) {
          const el = line("feature", "");
          el.append(`${name}: computed P(bull) = `, h("b", "", String(p.p_bull.toFixed ? p.p_bull.toFixed(2) : p.p_bull)));
        } else {
          line("warn", `${name}: feature model unavailable (${p.error || "unknown"})`);
        }
        session[p.agent] = { ...(session[p.agent] || {}), features: p.features, p_bull: p.p_bull };
        break;
      }
      case "agent_done": {
        session[p.agent] = {
          ...(session[p.agent] || {}),
          reasoning: p.reasoning, stance: p.stance, confidence: p.confidence,
        };
        if (!p.reasoning) break;
        const card = h("div", "agent");
        const head = h("div", "head");
        head.appendChild(h("b", "", AGENT_LABELS[p.agent] || p.agent));
        if (p.stance) {
          const st = h("span", "stance", p.stance + (p.confidence != null ? ` - ${p.confidence}` : ""));
          st.style.color = STANCE_COLORS[p.stance] || "#94a3b8";
          head.appendChild(st);
        }
        if (p.fallback) head.appendChild(h("span", "dim", "(templated)"));
        card.appendChild(head);
        card.appendChild(h("div", "body", p.reasoning));
        els.feed.appendChild(card);
        els.feed.scrollTop = els.feed.scrollHeight;
        break;
      }
      case "sentiment": {
        const holder = h("div");
        const bar = h("div", "bar");
        const bull = h("span", "bull"); bull.style.width = `${p.bull}%`;
        const bear = h("span", "bear"); bear.style.width = `${p.bear}%`;
        bar.append(bull, bear);
        holder.append(bar, h("div", "barlabel", `council sentiment: ${p.bull}% bull / ${p.bear}% bear`));
        els.feed.appendChild(holder);
        break;
      }
      case "chairman":
        renderDirectAnswer(currentIntent, session, p);
        renderVerdict(p);
        break;
      case "warning":
        line("warn", `warning: ${p.message}`);
        break;
      case "error":
        line("err", `The council hit a problem: ${p.message}`);
        break;
      case "done":
        line("dim", `council adjourned - ${p.latency_ms} ms${p.degraded ? " - DEGRADED" : ""}`);
        setBusy(false);
        break;
    }
  }

  function renderDirectAnswer(intent, sess, v) {
    const build = DIRECT_ANSWER_BUILDERS[intent.category];
    if (!build) return; // "general" or "verdict": no shortcut, full report stands
    const text = build(sess, v);
    if (!text) return; // that agent's data never arrived (degraded run) - say nothing rather than guess
    const card = h("div", "agent direct-answer");
    card.style.borderColor = "#7c3aed";
    const head = h("div", "head");
    head.appendChild(h("b", "", "Enma's straight answer"));
    card.appendChild(head);
    card.appendChild(h("div", "body", text));
    els.feed.appendChild(card);
    els.feed.scrollTop = els.feed.scrollHeight;
  }

  function renderVerdict(v) {
    const color = ACTION_COLORS[v.action] || "#e2e8f0";
    const card = h("div", "verdict");
    card.style.borderColor = color;

    line("enma", "The Chairman has computed the verdict:");
    const action = h("div", "action", v.action);
    action.style.color = color;
    card.appendChild(action);

    if (v.entry != null && (v.action === "BUY" || v.action === "SELL")) {
      const lv = h("div", "nums");
      lv.append("entry ", h("b", "", String(v.entry)), " - target ", h("b", "", String(v.target)),
                " - stop ", h("b", "", String(v.stop)), ` - R:R ${v.risk_reward}`);
      card.appendChild(lv);
    }
    const nums = h("div", "nums");
    nums.append("P(bull) ", h("b", "", String(v.p_bull_calibrated)),
                " - EV ", h("b", "", String(v.expected_value)),
                ` - edge ${v.edge} vs hurdle ${v.hurdle_tau}`);
    card.appendChild(nums);
    if (v.position_size_pct != null) {
      card.appendChild(h("div", "nums",
        `Kelly f* ${v.kelly_fraction} -> size ${(v.position_size_pct * 100).toFixed(1)}% of capital`));
    }
    if (v.rationale) card.appendChild(h("div", "rationale", v.rationale));

    const weights = Object.entries(v.agent_weights || {})
      .map(([a, w]) => `${(AGENT_LABELS[a] || a).split(" ").pop()} ${w}`).join(" - ");
    card.appendChild(h("div", "meta",
      `weights: ${weights}\ncalibration confidence: ${v.calibration_confidence}`));
    card.appendChild(h("div", "disclaimer", v.disclaimer || "AI analysis, not investment advice."));

    els.feed.appendChild(card);
    els.feed.scrollTop = els.feed.scrollHeight;
  }

  // ---- ask flow -------------------------------------------------------------
  function setBusy(b) {
    busy = b;
    els.orb.classList.toggle("busy", b);
    els.ask.disabled = b;
  }

  const ACK_BY_INTENT = {
    general: "Let me convene the council on this one.",
    technical: "Let's look at what's driving the recent price action.",
    risk: "Let me check the risk framing on this one.",
    valuation: "Let me check what the fundamentals say.",
    macro: "Let me check the macro backdrop.",
    verdict: "Let me put that to the council - the math will answer, I'll narrate.",
  };

  function ask() {
    if (busy) return;
    const manual = EnmaTickers.normalise(els.symInput.value);
    const detected = manual ? null : EnmaTickers.detect();
    if (detected && detected.unsupported) {
      els.symRow.classList.add("show");
      line("warn", `This looks like a ${detected.exchange} listing - Quorum currently only `
        + "covers NSE/BSE/NASDAQ/NYSE. Type a ticker on one of those below if you meant a different stock.");
      return;
    }
    const target = manual || detected;
    if (!target) {
      els.symRow.classList.add("show");
      line("warn", "I couldn't read a ticker from this page - type one below and ask again.");
      return;
    }
    setSymbolChip(target);
    const query = els.q.value.trim();
    els.q.value = "";
    session = {};
    currentIntent = EnmaIntent.classify(query);
    if (query) line("dim", `you: ${query}`);
    line("enma", ACK_BY_INTENT[currentIntent.category] || ACK_BY_INTENT.general);
    setBusy(true);

    const port = chrome.runtime.connect({ name: "enma-analyze" });
    port.onMessage.addListener((msg) => {
      if (msg.type === "event") renderEvent(msg.event, msg.payload);
      else if (msg.type === "bridge-error") { line("err", msg.message); setBusy(false); }
      else if (msg.type === "stream-end") setBusy(false);
    });
    port.onDisconnect.addListener(() => setBusy(false));
    port.postMessage({ symbol: target.symbol, exchange: target.exchange, query });
  }

  function setSymbolChip(t) {
    if (t && t.unsupported) els.chip.textContent = `${t.exchange} (unsupported)`;
    else els.chip.textContent = t ? `${t.exchange}:${t.symbol}` : "no ticker";
  }

  // ---- mount / toggle -------------------------------------------------------
  function build() {
    const host = h("div");
    host.id = "enma-host";
    const shadow = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = CSS;

    const wrap = h("div", "wrap");
    const header = h("header");
    const orb = h("span", "orb");
    const title = h("b", "", "Enma");
    // Version in the header: stale-code confusion (extension reloaded but the
    // tab's injected scripts not refreshed, or vice versa) is invisible
    // otherwise - this makes "which code is this tab actually running?"
    // answerable at a glance.
    const ver = chrome.runtime.getManifest().version;
    const sub = h("span", "sub", `math decides - Enma narrates - v${ver}`);
    const chip = h("span", "chip", "no ticker");
    const close = h("button", "close", "x");
    close.addEventListener("click", toggle);
    header.append(orb, title, sub, chip, close);

    const feed = h("div", "feed");

    const footer = h("footer");
    const row = h("div", "row");
    const q = h("input");
    q.placeholder = 'Ask about this stock... (or just press Ask)';
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); e.stopPropagation(); });
    const askBtn = h("button", "ask", "Ask");
    askBtn.addEventListener("click", ask);
    row.append(q, askBtn);
    const symRow = h("div", "row symrow");
    const symInput = h("input");
    symInput.placeholder = "Ticker, e.g. RELIANCE or NSE:INFY";
    symInput.addEventListener("keydown", (e) => { if (e.key === "Enter") ask(); e.stopPropagation(); });
    symRow.append(symInput);
    footer.append(row, symRow);

    wrap.append(header, feed, footer);
    shadow.append(style, wrap);

    // simple drag by header
    let drag = null;
    header.addEventListener("mousedown", (e) => {
      if (e.target === close) return;
      const r = wrap.getBoundingClientRect();
      drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!drag) return;
      wrap.style.left = `${e.clientX - drag.dx}px`;
      wrap.style.top = `${e.clientY - drag.dy}px`;
      wrap.style.right = "auto";
    });
    window.addEventListener("mouseup", () => { drag = null; });

    els = { orb, chip, feed, q, ask: askBtn, symRow, symInput };
    return host;
  }

  function toggle() {
    if (root && root.isConnected) {
      root.remove();
      return;
    }
    if (!root) {
      root = build();
      const t = EnmaTickers.detect();
      setSymbolChip(t);
      document.documentElement.appendChild(root);
      if (t && t.unsupported) {
        line("enma", `Hey - this looks like a ${t.exchange} listing. I currently only cover `
          + "NSE/BSE/NASDAQ/NYSE, sorry. Type a ticker on one of those below if you meant a different one.");
        els.symRow.classList.add("show");
      } else if (t) {
        line("enma", `Hey - I can see ${t.symbol} on screen. Ask me anything about it, or just hit Ask for the full council read.`);
      } else {
        line("enma", "Hey - I couldn't auto-read a ticker here. Type one below and ask away.");
        els.symRow.classList.add("show");
      }
    } else {
      document.documentElement.appendChild(root);
      const t = EnmaTickers.detect();
      setSymbolChip(t);
    }
    els.q.focus();
  }

  return { toggle };
})();
