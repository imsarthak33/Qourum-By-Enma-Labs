// Question -> intent classifier (E1, doc 08 par.4/par.7).
//
// Zero-LLM, keyword-based, deterministic. This is Enma "interpreting" and
// "selecting" per the boundary rule - it decides WHICH agent's already-
// computed facts answer the question. It never scores, weighs, or invents
// anything; ties/no-match fall back to "general" (today's full-report
// behaviour), so an unrecognised question never breaks, it just doesn't
// get a shortcut answer.

const EnmaIntent = (() => {
  // Order matters: first matching category wins, so put the more specific
  // buckets (risk, valuation) ahead of the broad "why did it move" bucket.
  const RULES = [
    {
      category: "risk",
      words: ["risk", "stop loss", "stop-loss", "stoploss", "safe", "safety",
              "downside", "worst case", "lose", "loss", "how much can i",
              "position size", "sizing", "kelly"],
    },
    {
      category: "valuation",
      words: ["valuation", "expensive", "cheap", "overvalued", "undervalued",
              "pe ratio", "p/e", "price to book", "p/b", "fair value", "worth",
              "fundamentals", "earnings", "roe"],
    },
    {
      category: "macro",
      words: ["macro", "sector", "rates", "rate hike", "fii", "dii", "flows",
              "market wide", "rotation", "nifty"],
    },
    {
      category: "verdict",
      words: ["should i buy", "should i sell", "buy or sell", "entry point",
              "should i invest", "verdict", "final call", "what's the call"],
    },
    {
      category: "technical",
      words: ["pattern", "chart", "trend", "technical", "momentum",
              "breakout", "support", "resistance", "why did", "why is",
              "reason behind", "moved", "increase", "decrease", "rally",
              "drop", "dip", "spike", "volume"],
    },
  ];

  function classify(question) {
    const q = (question || "").toLowerCase().trim();
    if (!q) return { category: "general" };
    for (const rule of RULES) {
      if (rule.words.some((w) => q.includes(w))) return { category: rule.category };
    }
    return { category: "general" };
  }

  return { classify };
})();
