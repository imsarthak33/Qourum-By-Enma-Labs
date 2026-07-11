"""The Enma bridge (`quorum serve`, doc 08 §5). The quant core is exercised
elsewhere; here we pin the HTTP contract the extension depends on - SSE framing,
event pass-through, CORS scoping to extension origins, token gating, and that a
computation error surfaces as an `error` event, never a traceback in the stream.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest

from quorum.serve import build_server


async def _fake_analyze(symbol, query="", exchange="NSE", config=None, on_event=None, **_):
    """Stand-in for quorum.analyze: emits the same event shape the orchestrator
    does, with no network or quant-core dependency."""
    on_event({"event": "debate_start", "debate_id": "t", "symbol": symbol})
    on_event({"event": "feature_ready", "agent": "technician", "p_bull": 0.68})
    on_event({"event": "chairman", "action": "WAIT", "p_bull_calibrated": 0.58, "edge": 0.08})
    on_event({"event": "done", "debate_id": "t", "latency_ms": 3})


async def _boom_analyze(symbol, on_event=None, **_):
    on_event({"event": "debate_start", "symbol": symbol})
    raise RuntimeError("feature model exploded")


class _FakeRoastResult:
    """Mirrors quorum.roast.RoastResult.to_json without the quant core."""
    def __init__(self, entries):
        self.entries = entries

    def to_json(self):
        return {
            "archetype": "The Mixed Book - no single tell dominates yet.",
            "tells": [f"read {len(self.entries)} names"],
            "reads": [{"symbol": sym, "exchange": exch, "side": side, "ok": True,
                       "error": None, "action": "WAIT", "p_bull": 0.5, "edge": 0.0}
                      for (exch, sym, side) in self.entries],
        }


def _fake_roast(entries, config=None):
    return _FakeRoastResult(entries)


def _boom_roast(entries, config=None):
    raise RuntimeError("roast blew up")


def _parse_sse(text: str) -> list[tuple[str, str]]:
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = data = ""
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        frames.append((event, data))
    return frames


@pytest.fixture
def serve():
    """Start bridge servers on ephemeral ports; tear them all down after."""
    started: list[Any] = []

    def _start(analyze_fn=_fake_analyze, roast_fn=_fake_roast, **kw):
        srv = build_server(host="127.0.0.1", port=0, analyze_fn=analyze_fn,
                           roast_fn=roast_fn, **kw)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    yield _start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def test_health_ok(serve):
    base = serve()
    r = httpx.get(base + "/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["service"] == "quorum-serve"


def test_analyze_streams_events_in_order(serve):
    base = serve()
    r = httpx.get(base + "/analyze?symbol=reliance", timeout=5)
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/event-stream"
    frames = _parse_sse(r.text)
    names = [e for e, _ in frames]
    assert names == ["debate_start", "feature_ready", "chairman", "done"]
    # Symbol is passed through untouched (bridge does not upper-case; engine does).
    assert '"symbol": "reliance"' in frames[0][1]
    assert '"action": "WAIT"' in frames[2][1]


def test_missing_symbol_is_400(serve):
    base = serve()
    r = httpx.get(base + "/analyze", timeout=5)
    assert r.status_code == 400
    assert "symbol" in r.json()["error"]


def test_unknown_route_is_404(serve):
    base = serve()
    r = httpx.get(base + "/nope", timeout=5)
    assert r.status_code == 404


def test_cors_allows_extension_origin_only(serve):
    base = serve()
    ext = httpx.get(base + "/health",
                    headers={"Origin": "chrome-extension://abcdef"}, timeout=5)
    assert ext.headers.get("access-control-allow-origin") == "chrome-extension://abcdef"

    web = httpx.get(base + "/health",
                    headers={"Origin": "https://evil.example"}, timeout=5)
    assert "access-control-allow-origin" not in web.headers


def test_extra_origin_can_be_allowed(serve):
    base = serve(extra_origins=["http://localhost:5173"])
    r = httpx.get(base + "/health",
                  headers={"Origin": "http://localhost:5173"}, timeout=5)
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_preflight_options(serve):
    base = serve()
    r = httpx.request("OPTIONS", base + "/analyze",
                      headers={"Origin": "chrome-extension://abcdef"}, timeout=5)
    assert r.status_code == 204
    assert r.headers.get("access-control-allow-origin") == "chrome-extension://abcdef"


def test_token_gate(serve):
    base = serve(token="s3cret")
    assert httpx.get(base + "/analyze?symbol=INFY", timeout=5).status_code == 401
    assert httpx.get(base + "/analyze?symbol=INFY&token=wrong", timeout=5).status_code == 401
    ok = httpx.get(base + "/analyze?symbol=INFY&token=s3cret", timeout=5)
    assert ok.status_code == 200
    assert [e for e, _ in _parse_sse(ok.text)][-1] == "done"


def test_token_via_bearer_header(serve):
    base = serve(token="s3cret")
    ok = httpx.get(base + "/analyze?symbol=INFY", timeout=5,
                   headers={"Authorization": "Bearer s3cret"})
    assert ok.status_code == 200


def test_compute_error_becomes_error_event(serve):
    base = serve(analyze_fn=_boom_analyze)
    r = httpx.get(base + "/analyze?symbol=INFY", timeout=5)
    assert r.status_code == 200  # headers already sent before the failure
    frames = _parse_sse(r.text)
    assert frames[0][0] == "debate_start"
    assert frames[-1][0] == "error"
    assert "exploded" in frames[-1][1]


# --- /roast route (growth plan Horizon 1: watchlist roast in the overlay) ----

def test_roast_returns_json_reads_and_dna(serve):
    base = serve()
    # params= percent-encodes, so the +/- side markers survive (a bare + in a
    # query string decodes to a space - callers must encodeURIComponent).
    r = httpx.get(base + "/roast",
                  params={"symbols": "+RELIANCE,NASDAQ:AAPL,-TCS"}, timeout=5)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/json"
    body = r.json()
    # Entries parsed with side + exchange, order preserved.
    assert [x["symbol"] for x in body["reads"]] == ["RELIANCE", "AAPL", "TCS"]
    assert [x["side"] for x in body["reads"]] == [1, 0, -1]
    assert body["reads"][1]["exchange"] == "NASDAQ"
    assert body["archetype"] and isinstance(body["tells"], list)


def test_roast_whitespace_separated_also_works(serve):
    base = serve()
    r = httpx.get(base + "/roast", params={"symbols": "RELIANCE INFY"}, timeout=5)
    assert [x["symbol"] for x in r.json()["reads"]] == ["RELIANCE", "INFY"]


def test_roast_missing_symbols_is_400(serve):
    base = serve()
    r = httpx.get(base + "/roast", timeout=5)
    assert r.status_code == 400
    assert "symbols" in r.json()["error"]


def test_roast_error_becomes_500_not_traceback(serve):
    base = serve(roast_fn=_boom_roast)
    r = httpx.get(base + "/roast?symbols=RELIANCE", timeout=5)
    assert r.status_code == 500
    assert r.json()["error"] == "roast blew up"


def test_roast_is_token_gated(serve):
    base = serve(token="s3cret")
    assert httpx.get(base + "/roast?symbols=RELIANCE", timeout=5).status_code == 401
    ok = httpx.get(base + "/roast?symbols=RELIANCE&token=s3cret", timeout=5)
    assert ok.status_code == 200


def test_roast_cors_extension_only(serve):
    base = serve()
    r = httpx.get(base + "/roast?symbols=RELIANCE",
                  headers={"Origin": "chrome-extension://abcdef"}, timeout=5)
    assert r.headers.get("access-control-allow-origin") == "chrome-extension://abcdef"
