"""Quorum CLI (01_PRD §5.1): `quorum analyze TATAMOTORS`.

Probabilities render the moment each quant feature model resolves
(feature_ready), before narration lands - the sentiment display is driven by
calibrated probabilities, never by LLM self-reported confidence.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import EXAMPLE_CONFIG, QuorumConfig
from .models import DISCLAIMER

app = typer.Typer(
    name="quorum",
    help="Quorum - an auditable AI debate engine for stock analysis. "
         "Math decides, LLMs narrate.",
    no_args_is_help=True,
)
console = Console()

AGENT_LABELS = {
    "technician": "The Technician",
    "fundamentalist": "The Fundamentalist",
    "macro": "The Macro Oracle",
    "devils_advocate": "The Devil's Advocate",
    "risk": "The Risk Ranger",
}

STANCE_STYLE = {"BULL": "bold green", "BEAR": "bold red", "NEUTRAL": "bold yellow"}


# ASCII-safe glyphs: legacy Windows consoles (cp1252) can't encode box/check
# characters and rich's legacy renderer crashes on them.
def _sentiment_bar(bull: int, width: int = 30) -> str:
    filled = round(width * bull / 100)
    return f"[green]{'#' * filled}[/green][red]{'-' * (width - filled)}[/red] {bull}% bull"


def _render_event(ev: dict) -> None:
    kind = ev.get("event")
    if kind == "debate_start":
        console.print(f"\n[bold]Convening the council on [cyan]{ev['symbol']}[/cyan]...[/bold]")
    elif kind == "fact_pack":
        missing = [k for k, v in ev["sources"].items() if v != "ok"]
        note = f" [dim](missing: {', '.join(missing)})[/dim]" if missing else ""
        console.print(f"[dim]fact pack ready{note}[/dim]")
    elif kind == "feature_ready":
        agent = AGENT_LABELS.get(ev["agent"], ev["agent"])
        if ev.get("triggered") is False:
            console.print(f"  [dim]{agent}: divergence test did not fire (D="
                          f"{ev.get('features', {}).get('D')}) - silent this round[/dim]")
        elif ev.get("p_bull") is not None:
            console.print(f"  {agent}: computed P(bull) = [bold]{ev['p_bull']:.2f}[/bold]")
        else:
            console.print(f"  [yellow]{agent}: feature model unavailable "
                          f"({ev.get('error')})[/yellow]")
    elif kind == "agent_done":
        agent = AGENT_LABELS.get(ev["agent"], ev["agent"])
        stance = ev.get("stance")
        style = STANCE_STYLE.get(stance, "dim")
        header = f"[{style}]{stance or '-'}[/{style}]"
        conf = f" | {ev['confidence']}" if ev.get("confidence") is not None else ""
        fb = " [dim](templated)[/dim]" if ev.get("fallback") else ""
        console.print(f"\n[bold]{agent}[/bold] {header}{conf}{fb}")
        if ev.get("reasoning"):
            console.print(f"  {ev['reasoning']}")
    elif kind == "sentiment":
        console.print(f"\n{_sentiment_bar(ev['bull'])}")
    elif kind == "warning":
        console.print(f"[yellow]warning: {ev['message']}[/yellow]")
    elif kind == "error":
        console.print(f"[red]error: {ev['message']}[/red]")


# Cold-start transparency, load-bearing (growth plan Horizon 0): calibration
# confidence renders inside the verdict body, not as a footnote, with the
# honest n so thin data reads as "watch it grow" rather than false precision.
CALIB_STYLE = {"low": "yellow", "medium": "cyan", "high": "green"}
CALIB_NOTES = {
    "low": "young track record - treat probabilities as directional",
    "medium": "track record building - numbers firming up",
    "high": "fitted on a full outcome history",
}


def _calibration_line(level: str, resolved: Optional[int] = None) -> str:
    style = CALIB_STYLE.get(level, "yellow")
    n = f", n={resolved} resolved" if resolved is not None else ""
    return (f"[bold {style}]calibration {level.upper()}[/bold {style}]"
            f" ({CALIB_NOTES.get(level, '')}{n})")


def _render_verdict(v: dict, degraded: bool, latency_ms: int,
                    resolved: Optional[int] = None) -> None:
    action = v["action"]
    style = {"BUY": "green", "SELL": "red", "WAIT": "yellow",
             "AVOID": "red", "NO_CALL": "cyan"}.get(action, "white")
    lines = [f"[bold {style}]{action}[/bold {style}]"]
    if v.get("entry") is not None and action in ("BUY", "SELL"):
        lines.append(
            f"entry [bold]{v['entry']}[/bold] | target [bold]{v['target']}[/bold]"
            f" | stop [bold]{v['stop']}[/bold] | R:R {v.get('risk_reward')}"
        )
    lines.append(
        f"P(bull) [bold]{v['p_bull_calibrated']}[/bold] | EV {v['expected_value']}"
        f" | edge {v['edge']} vs hurdle {v['hurdle_tau']}"
    )
    lines.append(_calibration_line(v["calibration_confidence"], resolved))
    if v.get("position_size_pct") is not None:
        lines.append(f"Kelly f* {v['kelly_fraction']} -> size {v['position_size_pct']:.1%} of capital")
    weights = " | ".join(f"{AGENT_LABELS.get(a, a).split()[-1]} {w}"
                         for a, w in v.get("agent_weights", {}).items())
    lines.append(f"[dim]weights: {weights}[/dim]")
    lines.append(f"[dim]{'DEGRADED | ' if degraded else ''}{latency_ms} ms[/dim]")
    if v.get("rationale"):
        lines.append(f"\n{v['rationale']}")
    lines.append(f"\n[italic dim]{DISCLAIMER}[/italic dim]")
    console.print(Panel("\n".join(lines), title="The Chairman - computed verdict",
                        border_style=style))


@app.command()
def analyze(
    symbol: str = typer.Argument(..., help="NSE/BSE symbol, e.g. TATAMOTORS"),
    query: str = typer.Option("", "--query", "-q", help="Optional free-text question"),
    exchange: str = typer.Option("NSE", "--exchange", "-e"),
    share: bool = typer.Option(False, "--share",
                               help="Opt in: submit the resolved outcome (never the query "
                                    "or your identity) to the public leaderboard"),
    as_json: bool = typer.Option(False, "--json", help="Print the raw verdict JSON only"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """Convene the council on a stock and print the computed verdict."""
    config = QuorumConfig.load(config_path)
    from .orchestrator import run_debate

    on_event = _noop_event if as_json else _render_event
    debate = asyncio.run(
        run_debate(symbol, query=query, exchange=exchange, config=config,
                   on_event=on_event, share=share)
    )
    if debate.status == "failed":
        raise typer.Exit(code=1)
    if as_json:
        console.print_json(json.dumps({
            "debate_id": debate.debate_id,
            "symbol": debate.symbol,
            "status": debate.status,
            "verdict": debate.verdict.to_json() if debate.verdict else None,
        }))
    elif debate.verdict:
        from .storage import Storage

        resolved = Storage(config.db_path).track_record().get("resolved")
        _render_verdict(debate.verdict.to_json(), debate.degraded,
                        debate.latency_ms, resolved)


def _noop_event(_: dict) -> None:
    pass


@app.command()
def history(limit: int = typer.Option(15, "--limit", "-n")):
    """Show recent debates from the local database."""
    config = QuorumConfig.load()
    from .storage import Storage

    storage = Storage(config.db_path)
    rows = storage.recent_debates(limit)
    if not rows:
        console.print("[dim]No debates yet. Run: quorum analyze TATAMOTORS[/dim]")
        return
    table = Table(title="Recent debates")
    for col in ("when", "symbol", "action", "P(bull)", "edge", "status"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            (r["created_at"] or "")[:16].replace("T", " "),
            r["symbol"],
            r["action"] or "-",
            f"{r['p_bull']:.2f}" if r["p_bull"] is not None else "-",
            f"{r['edge']:.3f}" if r["edge"] is not None else "-",
            r["status"] + (" (degraded)" if r["degraded"] else ""),
        )
    console.print(table)


def _resolve_pass(config: QuorumConfig) -> None:
    """One outcome-tracking pass; shared by `resolve` and `batch`."""
    from . import leaderboard
    from .outcomes import check_outcomes
    from .storage import Storage

    storage = Storage(config.db_path)
    resolved = check_outcomes(storage, config)
    if not resolved:
        console.print("[dim]Nothing newly resolved.[/dim]")
        return
    for item in resolved:
        mark = "+" if item["correct"] else ("x" if item["correct"] is not None else "~")
        console.print(f"{mark} {item['symbol']}: {item['result']}")
        if item["share"]:
            payload = leaderboard.build_submission(item["row"], item["result"])
            ok = leaderboard.submit(config.supabase_url, config.supabase_anon_key, payload)
            console.print(f"  [dim]leaderboard submission "
                          f"{'accepted' if ok else 'failed (kept local)'}[/dim]")
    record = storage.track_record()
    if record["resolved"]:
        console.print(
            f"\nRolling 90d: {record['accuracy_pct']}% accuracy over "
            f"{record['resolved']} resolved | Brier {record['brier_score']} "
            f"[dim](calibration confidence: {record['calibration_confidence']})[/dim]"
        )


@app.command()
def resolve():
    """Check open verdicts against price history; update the outcome log,
    Hedge weights, and (with --share debates) the public leaderboard."""
    _resolve_pass(QuorumConfig.load())


# Growth plan Horizon 0 basket: liquid names on both supported market models,
# small enough for real receipts without noise. Override with --basket.
DEFAULT_BASKET: list[tuple[str, str]] = [
    ("NSE", "RELIANCE"), ("NSE", "INFY"), ("NSE", "HDFCBANK"),
    ("NSE", "TCS"), ("NSE", "ICICIBANK"),
    ("NASDAQ", "AAPL"), ("NASDAQ", "MSFT"), ("NASDAQ", "NVDA"),
    ("NASDAQ", "GOOGL"), ("NASDAQ", "AMZN"),
]


def _parse_basket(path: Path) -> list[tuple[str, str]]:
    """One entry per line, `EXCHANGE:SYMBOL` (bare symbols default to NSE);
    blank lines and # comments ignored. utf-8-sig: Windows editors (and
    PowerShell's Out-File) routinely write a BOM, which must not become part
    of the first symbol."""
    entries: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        exchange, _, symbol = line.rpartition(":")
        if not exchange:
            exchange = "NSE"
        entries.append((exchange.strip().upper(), symbol.strip().upper()))
    return entries


@app.command()
def batch(
    basket: Optional[Path] = typer.Option(
        None, "--basket", "-b",
        help="Basket file: one EXCHANGE:SYMBOL per line (bare symbol = NSE). "
             "Defaults to a built-in 10-name NSE+NASDAQ basket."),
    share: bool = typer.Option(
        True, "--share/--no-share",
        help="Mark each debate for public-leaderboard submission on resolution "
             "(outcome only - never a query or your identity)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """Daily outcome-accumulation pass (growth plan Horizon 0): analyze every
    basket symbol, then resolve open verdicts. One failing symbol never aborts
    the rest - built to run unattended under a scheduler."""
    config = QuorumConfig.load(config_path)
    from .orchestrator import run_debate

    entries = _parse_basket(basket) if basket else DEFAULT_BASKET
    analyzed = 0
    for exchange, symbol in entries:
        try:
            debate = asyncio.run(
                run_debate(symbol, exchange=exchange, config=config,
                           on_event=_noop_event, share=share)
            )
        except Exception as exc:  # a bad symbol/feed must not kill the pass
            console.print(f"x {exchange}:{symbol} crashed: {exc}")
            continue
        if debate.status == "failed" or debate.verdict is None:
            console.print(f"x {exchange}:{symbol} failed")
            continue
        v = debate.verdict.to_json()
        console.print(
            f"+ {exchange}:{symbol}: {v['action']} | P(bull) {v['p_bull_calibrated']}"
            f" | edge {v['edge']} | calib {v['calibration_confidence']}"
            + (" (degraded)" if debate.degraded else "")
        )
        analyzed += 1
    console.print(f"\nanalyzed {analyzed}/{len(entries)}")
    _resolve_pass(config)
    if entries and analyzed == 0:
        raise typer.Exit(code=1)


@app.command("leaderboard")
def leaderboard_cmd():
    """Show the public community leaderboard (accuracy + calibration quality)."""
    config = QuorumConfig.load()
    from . import leaderboard

    if not config.supabase_url:
        console.print("[yellow]No leaderboard configured yet - set supabase_url / "
                      "supabase_anon_key in quorum.yaml (or QUORUM_SUPABASE_URL / "
                      "QUORUM_SUPABASE_ANON_KEY).[/yellow]")
        raise typer.Exit(code=1)
    stats = leaderboard.fetch_stats(config.supabase_url, config.supabase_anon_key)
    if stats is None:
        console.print("[red]Leaderboard unreachable (local runs are unaffected).[/red]")
        raise typer.Exit(code=1)
    n = stats.get("n_resolved") or 0
    console.print(f"\n[bold]Quorum community leaderboard[/bold] (rolling 90d)")
    console.print(f"resolved calls: {n} | accuracy: {stats.get('accuracy_pct') or '-'}% | "
                  f"Brier: {stats.get('brier_score') or '-'} | "
                  f"log-loss: {stats.get('log_loss') or '-'}")
    if stats["calibration_confidence"] != "high":
        console.print("[dim]calibration confidence is "
                      f"{stats['calibration_confidence']} - the model is still learning; "
                      "numbers firm up after ~250 resolved outcomes.[/dim]")
    calls = leaderboard.fetch_recent_calls(config.supabase_url, config.supabase_anon_key, limit=10)
    if calls:
        table = Table(title="Latest public calls")
        for col in ("symbol", "action", "P(bull)", "result"):
            table.add_column(col)
        for c in calls:
            table.add_row(c["symbol"], c["action"],
                          str(c.get("p_bull_calibrated") or "-"), c.get("result") or "-")
        console.print(table)


@app.command()
def calibrate():
    """Weekly calibration refit: isotonic curves per agent from the local
    outcome log (identity seed kept until enough outcomes resolve)."""
    config = QuorumConfig.load()
    from .outcomes import calibration_refit
    from .storage import Storage

    storage = Storage(config.db_path)
    fitted = calibration_refit(storage)
    if not fitted:
        console.print("[dim]No resolved outcomes yet - curves stay at the identity seed "
                      "(the model is still learning; ~200-300 outcomes per agent needed).[/dim]")
    for agent, n in fitted.items():
        console.print(f"{agent}: curve fit on {n} outcomes")


@app.command()
def init(path: Path = typer.Option(Path("quorum.yaml"), "--path", "-p")):
    """Write an example quorum.yaml config to get started."""
    if path.exists():
        console.print(f"[yellow]{path} already exists - not overwriting.[/yellow]")
        raise typer.Exit(code=1)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    console.print(f"Wrote {path}. Set at least one API key env var "
                  f"(e.g. GROQ_API_KEY) and run: quorum analyze TATAMOTORS")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host",
                             help="Bind address; localhost only by default (do not expose)"),
    port: int = typer.Option(8756, "--port", "-p"),
    token: Optional[str] = typer.Option(
        None, "--token",
        help="Optional shared secret; if set, /analyze requires ?token=... or a Bearer header"),
    allow_origin: Optional[list[str]] = typer.Option(
        None, "--allow-origin",
        help="Extra CORS origin to allow (repeatable). chrome-/moz-extension origins "
             "are always allowed; normal web pages never are."),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
):
    """Run the local council bridge for the Enma overlay (doc 08): a
    localhost-only HTTP/SSE endpoint over `quorum analyze`. No hosted backend -
    your machine computes, your own keys pay for inference. Math still decides."""
    config = QuorumConfig.load(config_path)
    from .serve import run_server

    run_server(host=host, port=port, config=config, token=token,
               extra_origins=list(allow_origin or []), echo=console.print)


@app.command()
def version():
    """Print the Quorum version."""
    console.print(f"quorum {__version__}")


if __name__ == "__main__":
    app()
