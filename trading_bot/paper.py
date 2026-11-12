"""Paper trader: runs the same Engine on the latest daily bars and persists state to a JSON ledger.

Run once per trading day after the close. Missed days are replayed in order, so
running it late is fine. No real orders are ever sent from here.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from .config import Config
from .data import features, load_bars
from .engine import Bar, Engine, Order, Portfolio, Trade, position_from_dict, position_to_dict


def load_ledger(path: Path, cfg: Config) -> tuple[Portfolio, dict]:
    if not path.exists():
        return Portfolio(cash=cfg.starting_cash), {"last_run": None, "equity_history": []}
    raw = json.loads(path.read_text())
    pf = Portfolio(
        cash=raw["cash"],
        positions=[position_from_dict(p) for p in raw["positions"]],
        trades=[
            Trade(**{**t, "entry_date": date.fromisoformat(t["entry_date"]),
                     "exit_date": date.fromisoformat(t["exit_date"])})
            for t in raw["trades"]
        ],
        pending=[Order(**o) for o in raw["pending"]],
        last_rebalance=raw.get("last_rebalance", ""),
    )
    return pf, {"last_run": raw.get("last_run"), "equity_history": raw.get("equity_history", [])}


def save_ledger(path: Path, pf: Portfolio, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "last_run": meta["last_run"],
        "cash": round(pf.cash, 2),
        "equity": round(pf.equity(), 2),
        "positions": [position_to_dict(p) for p in pf.positions],
        "pending": [asdict(o) for o in pf.pending],
        "last_rebalance": pf.last_rebalance,
        "trades": [
            {**asdict(t), "entry_date": t.entry_date.isoformat(), "exit_date": t.exit_date.isoformat()}
            for t in pf.trades
        ],
        "equity_history": meta["equity_history"],
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def run_paper(cfg: Config, ledger: Path, quotes_path: Path | None = None) -> Engine:
    pf, meta = load_ledger(ledger, cfg)
    engine = Engine(cfg, pf)
    frames = {s: features(load_bars(s, "2015-01-01", refresh=True), cfg, s, refresh=True)
              for s in cfg.symbols}
    quotes = json.loads(quotes_path.read_text()) if quotes_path else {}

    for s, f in frames.items():
        row = f.iloc[-1]
        trend = "above" if row.close > row.sma else "below"
        engine.log.append(
            f"{f.index[-1].date()} {s:<5} close {row.close:8.2f}  RSI({cfg.rsi_period}) {row.rsi:5.1f}  "
            f"{trend} {cfg.trend_sma}-SMA {row.sma:8.2f}  vol {row.vol:.0%}"
        )

    dates = sorted(set().union(*(f.index for f in frames.values())))
    last = pd.Timestamp(meta["last_run"]) if meta["last_run"] else dates[-2]  # first run: today only
    todo = [d for d in dates if d > last]
    if not todo:
        engine.log.append(f"already up to date through {meta['last_run']}")
        return engine
    for ts in todo:
        bars = {s: Bar(**f.loc[ts].to_dict()) for s, f in frames.items() if ts in f.index}
        # real quotes only apply to the most recent day
        engine.step(ts.date(), bars, quotes if ts == todo[-1] else None)
        meta["equity_history"].append([ts.date().isoformat(), round(engine.pf.equity(), 2)])
    meta["last_run"] = todo[-1].date().isoformat()
    save_ledger(ledger, engine.pf, meta)
    return engine


def summary(engine: Engine, cfg: Config) -> str:
    pf = engine.pf
    lines = [
        f"Equity ${pf.equity():,.2f}  (cash ${pf.cash:,.2f}, start ${cfg.starting_cash:,.2f}, "
        f"return {pf.equity() / cfg.starting_cash - 1:+.2%})",
        "Open positions:" if pf.positions else "Open positions: none",
    ]
    for p in pf.positions:
        lines.append(f"  {p.side:5} {p.qty:g}x {p.label}  entry {p.entry_price:.2f}  "
                     f"mark {p.mark:.2f}  pnl {p.pnl_pct():+.0%}")
    lines.append("Orders queued for next session:" if pf.pending else "Orders queued for next session: none")
    for o in pf.pending:
        lines.append(f"  {o.action} {o.symbol} {o.direction or o.reason}")
    return "\n".join(lines)
