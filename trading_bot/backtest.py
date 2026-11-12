from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from .config import Config
from .data import features, load_bars
from .engine import Bar, Engine


def build_frames(cfg: Config, start: str, refresh: bool = False) -> dict[str, pd.DataFrame]:
    return {s: features(load_bars(s, start, refresh), cfg) for s in cfg.symbols}


def run(cfg: Config, frames: dict[str, pd.DataFrame], start: str | None = None, end: str | None = None):
    dates = sorted(set().union(*(f.index for f in frames.values())))
    dates = [d for d in dates if (start is None or d >= pd.Timestamp(start)) and (end is None or d <= pd.Timestamp(end))]
    engine = Engine(cfg)
    equity, invested = [], []
    for ts in dates:
        bars = {
            s: Bar(**f.loc[ts].to_dict()) for s, f in frames.items() if ts in f.index
        }
        engine.step(ts.date(), bars)
        equity.append((ts, engine.pf.equity()))
        invested.append(bool(engine.pf.positions))
    eq = pd.Series(dict(equity), name="equity")
    eq.attrs["exposure"] = sum(invested) / len(invested) if invested else 0.0
    trades = pd.DataFrame([asdict(t) for t in engine.pf.trades])
    return engine, eq, trades


def metrics(eq: pd.Series, trades: pd.DataFrame, starting_cash: float) -> dict:
    if eq.empty:
        return {}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    final = eq.iloc[-1]
    rets = eq.pct_change().dropna()
    dd = eq / eq.cummax() - 1
    m = {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "final_equity": round(final, 2),
        "total_return": final / starting_cash - 1,
        "cagr": (final / starting_cash) ** (1 / years) - 1 if final > 0 else -1.0,
        "max_drawdown": dd.min(),
        "sharpe": (rets.mean() / rets.std() * math.sqrt(252)) if rets.std() > 0 else 0.0,
        "trades": len(trades),
        "exposure": eq.attrs.get("exposure", 0.0),
    }
    if len(trades):
        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl <= 0]
        m["win_rate"] = len(wins) / len(trades)
        m["avg_win"] = wins.pnl.mean() if len(wins) else 0.0
        m["avg_loss"] = losses.pnl.mean() if len(losses) else 0.0
        gross_loss = -losses.pnl.sum()
        m["profit_factor"] = wins.pnl.sum() / gross_loss if gross_loss > 0 else float("inf")
        m["exit_reasons"] = trades.reason.value_counts().to_dict()
    return m


def benchmark(frames: dict[str, pd.DataFrame], eq: pd.Series, symbol: str) -> dict:
    close = frames[symbol]["close"].loc[eq.index[0]: eq.index[-1]]
    years = max((close.index[-1] - close.index[0]).days / 365.25, 1e-9)
    tot = close.iloc[-1] / close.iloc[0] - 1
    return {
        "symbol": symbol,
        "total_return": tot,
        "cagr": (1 + tot) ** (1 / years) - 1,
        "max_drawdown": (close / close.cummax() - 1).min(),
        "sharpe": (lambda r: r.mean() / r.std() * math.sqrt(252))(close.pct_change().dropna()),
    }


def save(eq: pd.Series, trades: pd.DataFrame, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    eq.to_csv(out / "equity.csv")
    trades.to_csv(out / "trades.csv", index=False)


def fmt(m: dict) -> str:
    lines = []
    for k, v in m.items():
        if isinstance(v, float) and k in {"total_return", "cagr", "max_drawdown", "win_rate", "exposure"}:
            v = f"{v:.1%}" if k in ("win_rate", "exposure") else f"{v:+.2%}"
        elif isinstance(v, float):
            v = f"{v:,.2f}"
        lines.append(f"  {k:<14} {v}")
    return "\n".join(lines)
