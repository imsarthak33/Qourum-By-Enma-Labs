"""`quorum serve` - the local council bridge for the Enma overlay (doc 08 §5).

A localhost-only HTTP endpoint that fronts `quorum.analyze()` and streams the
council's progress to a browser as Server-Sent Events. It computes NOTHING
itself: it re-emits the orchestrator's own event stream verbatim, so the Enma
extension consumes the exact same events the CLI does - one source of truth, no
re-mapping layer that could drift out of sync with the engine.

Track A discipline (00 §2, 08 §6): binds 127.0.0.1 only, no hosted backend, the
user's machine does the compute and the user's own keys pay for inference. Uses
the standard library only - no new dependency, keeping the one-command install
promise. cp1252 note: startup lines printed to the console stay ASCII-safe; the
SSE body is UTF-8 over HTTP and rendered by the browser, so it is unconstrained.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import quorum

from .config import QuorumConfig

AnalyzeFn = Callable[..., Awaitable[Any]]
RoastFn = Callable[..., Any]  # sync: roast(entries, config) -> RoastResult

# Only a browser *extension* may read responses cross-origin. A normal web page
# (an http/https origin) is refused, so a random site the user happens to visit
# can't silently drive the local council and burn their API quota via CORS.
_EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://")


def _is_extension_origin(origin: str | None) -> bool:
    return bool(origin) and origin.startswith(_EXTENSION_SCHEMES)


class _Handler(BaseHTTPRequestHandler):
    """One request per thread (ThreadingHTTPServer). The subclass built in
    `build_server` injects `config`, `analyze_fn`, `token`, `extra_origins`."""

    server_version = "quorum-serve/" + quorum.__version__

    # Injected by build_server via a dynamic subclass.
    config: QuorumConfig
    analyze_fn: AnalyzeFn
    roast_fn: RoastFn
    token: str | None
    extra_origins: set[str]

    # Silence the default stderr access log; serve() prints its own ASCII lines.
    def log_message(self, *_args: Any) -> None:  # noqa: D401
        pass

    # --- CORS -------------------------------------------------------------
    def _send_cors(self, origin: str | None) -> None:
        allow = origin if (_is_extension_origin(origin) or origin in self.extra_origins) else None
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802 (http.server naming)
        origin = self.headers.get("Origin")
        self.send_response(204)
        self._send_cors(origin)
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    # --- routing ----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        origin = self.headers.get("Origin")
        if route == "/health":
            self._send_json(200, {"ok": True, "service": "quorum-serve",
                                  "version": quorum.__version__}, origin)
        elif route == "/analyze":
            self._stream_analyze(parse_qs(parsed.query), origin)
        elif route == "/roast":
            self._do_roast(parse_qs(parsed.query), origin)
        else:
            self._send_json(404, {"error": "not found",
                                  "routes": ["/health", "/analyze?symbol=SYMBOL",
                                             "/roast?symbols=+RELIANCE,NASDAQ:AAPL"]}, origin)

    # --- helpers ----------------------------------------------------------
    def _send_json(self, code: int, payload: dict[str, Any], origin: str | None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors(origin)
        self.end_headers()
        self.wfile.write(body)

    def _send_sse(self, event: str, data: dict[str, Any]) -> None:
        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _bearer_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else None

    def _token_ok(self, qs: dict[str, list[str]]) -> bool:
        if self.token is None:
            return True
        supplied = (qs.get("token") or [None])[0] or self._bearer_token()
        return supplied == self.token

    def _do_roast(self, qs: dict[str, list[str]], origin: str | None) -> None:
        """A watchlist roast (doc 08, growth plan Horizon 1). Unlike /analyze
        this is a single batched JSON reply, not a stream: the roast is
        quant-only and computed in one shot. It computes nothing here - it
        calls the same `quorum.roast.roast` the CLI does and serialises the
        result verbatim."""
        if not self._token_ok(qs):
            return self._send_json(401, {"error": "invalid or missing token"}, origin)
        from .roast import parse_entry

        raw = (qs.get("symbols") or [""])[0]
        # Accept comma- or whitespace-separated entries (a URL-encoded space is
        # awkward for callers; a comma is the least-surprising default).
        entries = [e for e in (parse_entry(tok) for tok in raw.replace(",", " ").split()) if e]
        if not entries:
            return self._send_json(
                400, {"error": "symbols query param required, e.g. "
                      "?symbols=+RELIANCE,NASDAQ:AAPL,-TCS"}, origin)
        try:
            result = self.roast_fn(entries, self.config)
        except Exception as exc:  # never leak a traceback to the client
            return self._send_json(500, {"error": str(exc)}, origin)
        self._send_json(200, result.to_json(), origin)

    def _stream_analyze(self, qs: dict[str, list[str]], origin: str | None) -> None:
        symbol = (qs.get("symbol") or [""])[0].strip()
        if not symbol:
            return self._send_json(400, {"error": "symbol query param required"}, origin)
        if not self._token_ok(qs):
            return self._send_json(401, {"error": "invalid or missing token"}, origin)

        exchange = (qs.get("exchange") or ["NSE"])[0]
        query = (qs.get("query") or [""])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._send_cors(origin)
        self.end_headers()

        # The orchestrator calls on_event synchronously as each stage resolves;
        # we serialise each event straight to the wire. A write failure means
        # the client hung up - let it propagate to abort the debate.
        write_lock = threading.Lock()

        def on_event(ev: dict[str, Any]) -> None:
            with write_lock:
                self._send_sse(ev.get("event", "message"), ev)

        try:
            asyncio.run(self.analyze_fn(
                symbol, query=query, exchange=exchange,
                config=self.config, on_event=on_event,
            ))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return  # client disconnected mid-stream; nothing more to do
        except Exception as exc:  # never leak a traceback into the SSE stream
            try:
                self._send_sse("error", {"event": "error", "message": str(exc)})
            except OSError:
                pass


def build_server(
    host: str = "127.0.0.1",
    port: int = 8756,
    config: QuorumConfig | None = None,
    analyze_fn: AnalyzeFn | None = None,
    roast_fn: RoastFn | None = None,
    token: str | None = None,
    extra_origins: "list[str] | set[str] | None" = None,
) -> ThreadingHTTPServer:
    """Construct (but do not start) the bridge server. `analyze_fn`/`roast_fn`
    are injectable so tests can drive the HTTP contract without touching the
    network or quant core. Pass `port=0` for an ephemeral port (read it back
    from `server_address`)."""
    cfg = config or QuorumConfig.load()
    fn = analyze_fn or quorum.analyze

    def _default_roast(entries, config):
        from .roast import roast
        return roast(entries, config)

    handler = type("_QuorumBridgeHandler", (_Handler,), {
        "config": cfg,
        "analyze_fn": staticmethod(fn),  # keep it a plain callable, not a bound method
        "roast_fn": staticmethod(roast_fn or _default_roast),
        "token": token,
        "extra_origins": set(extra_origins or ()),
    })
    return ThreadingHTTPServer((host, port), handler)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8756,
    config: QuorumConfig | None = None,
    token: str | None = None,
    extra_origins: "list[str] | None" = None,
    echo: Callable[[str], None] = print,
) -> None:
    """Start the bridge and serve until interrupted. Console lines stay ASCII."""
    httpd = build_server(host, port, config, token=token, extra_origins=extra_origins)
    bound_port = httpd.server_address[1]
    echo(f"quorum serve: council bridge on http://{host}:{bound_port}")
    echo("  GET /health   GET /analyze?symbol=RELIANCE (SSE)   GET /roast?symbols=+RELIANCE,NASDAQ:AAPL")
    lock_note = " token required" if token else ""
    echo(f"  self-hosted, localhost only.{lock_note} Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        echo("")
        echo("quorum serve: stopped.")
    finally:
        httpd.server_close()
