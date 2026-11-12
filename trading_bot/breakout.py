"""Daily breakout scanner + backtest (long-only stocks).

Entry signal (evaluated on day t's close, filled at day t+1's OPEN):
  - close is a new `high_lookback`-day closing high
  - volume >= vol_mult x its `vol_lookback`-day average
  - close > its `trend_sma`-day SMA
  - market filter: SPY close > SPY 200-day SMA
Exit signal (on close, filled next open):
  - close < lowest close of the prior `exit_lookback` days, or
  - close <= entry * (1 - stop_pct)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

# Liquid US large caps (current list -> survivorship bias in backtests; see README).
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "ORCL",
    "CRM", "ADBE", "INTC", "CSCO", "QCOM", "TXN", "MU", "AMAT", "IBM", "NFLX",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "AXP", "V", "MA", "PYPL",
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "AMGN", "GILD",
    "WMT", "COST", "HD", "LOW", "TGT", "MCD", "SBUX", "NKE", "KO", "PEP",
    "PG", "XOM", "CVX", "COP", "CAT", "DE", "BA", "GE", "HON", "UPS",
    "LMT", "RTX", "DIS", "CMCSA", "T", "VZ", "F", "GM", "UBER", "SHOP",
]
CACHE = Path("data/cache/breakout_universe.pkl")


@dataclass
class BreakoutParams:
    high_lookback: int = 100
    vol_lookback: int = 50
    vol_mult: float = 1.5
    trend_sma: int = 200
    exit_lookback: int = 20
    stop_pct: float = 0.10
    max_positions: int = 5
    cost_bps: float = 5.0
    market_filter: bool = True


def load_universe(symbols: list[str], start: str = "2008-01-01", refresh: bool = False) -> dict[str, pd.DataFrame]:
    """OHLCV per symbol (adjusted), downloaded in one batch and cached."""
    if CACHE.exists() and not refresh:
        data = pd.read_pickle(CACHE)
        if set(symbols) | {"SPY"} <= set(data):
            return {s: data[s] for s in symbols + ["SPY"]}
    import yfinance as yf

    raw = yf.download(symbols + ["SPY"], start=start, progress=False, auto_adjust=True,
                      group_by="ticker", threads=True)
    data = {}
    for s in symbols + ["SPY"]:
        try:
            df = raw[s][["Open", "High", "Low", "Close", "Volume"]].dropna()
        except KeyError:
            continue
        if len(df) > 260:
            df.index = pd.to_datetime(df.index).tz_localize(None)
            data[s] = df
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    pd.to_pickle(data, CACHE)
    return {s: data[s] for s in symbols + ["SPY"] if s in data}


def signals(df: pd.DataFrame, p: BreakoutParams) -> pd.DataFrame:
    c, v = df["Close"], df["Volume"]
    prior_high = c.shift(1).rolling(p.high_lookback).max()
    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["open"] = df["Open"]
    out["entry"] = (
        (c > prior_high)
        & (v >= p.vol_mult * v.shift(1).rolling(p.vol_lookback).mean())
        & (c > c.rolling(p.trend_sma).mean())
    )
    out["exit_low"] = c.shift(1).rolling(p.exit_lookback).min()
    # strength for ranking candidates: % above the prior high, scaled by volume surge
    out["score"] = (c / prior_high - 1) * (v / v.shift(1).rolling(p.vol_lookback).mean())
    return out


def market_ok(spy: pd.DataFrame) -> pd.Series:
    c = spy["Close"]
    return c > c.rolling(200).mean()


def backtest(data: dict[str, pd.DataFrame], p: BreakoutParams, start: str, end: str | None = None,
             start_cash: float = 10_000.0):
    spy = data["SPY"]
    mkt = market_ok(spy)
    sig = {s: signals(df, p) for s, df in data.items() if s != "SPY"}
    dates = spy.loc[start:end].index
    cash, pos, trades, equity = start_cash, {}, [], []
    pending_buy: list[str] = []
    pending_sell: list[str] = []
    for d in dates:
        # fills at today's open
        for s in pending_sell:
            if s in pos and d in sig[s].index:
                px = sig[s].at[d, "open"] * (1 - p.cost_bps / 1e4)
                sh, entry_px, entry_d = pos.pop(s)
                cash += sh * px
                trades.append((s, entry_d, d, entry_px, px, px / entry_px - 1))
        pending_sell = []
        for s in pending_buy:
            if s in pos or len(pos) >= p.max_positions or d not in sig[s].index:
                continue
            eq_now = cash + sum(sh * sig[x].at[d, "open"] for x, (sh, _, _) in pos.items() if d in sig[x].index)
            budget = min(eq_now / p.max_positions, cash)
            px = sig[s].at[d, "open"] * (1 + p.cost_bps / 1e4)
            if budget > 1:
                pos[s] = (budget / px, px, d)
                cash -= budget
        pending_buy = []
        # end-of-day decisions
        for s, (sh, entry_px, _) in pos.items():
            row = sig[s].loc[d] if d in sig[s].index else None
            if row is not None and (row.close < row.exit_low or row.close <= entry_px * (1 - p.stop_pct)):
                pending_sell.append(s)
        if not p.market_filter or bool(mkt.get(d, False)):
            slots = p.max_positions - len(pos) + len(pending_sell)
            cands = [(sig[s].at[d, "score"], s) for s in sig
                     if s not in pos and d in sig[s].index and bool(sig[s].at[d, "entry"])]
            pending_buy = [s for _, s in sorted(cands, reverse=True)[:max(slots, 0)]]
        mv = sum(sh * sig[s].at[d, "close"] for s, (sh, _, _) in pos.items() if d in sig[s].index)
        equity.append((d, cash + mv, len(pos)))
    eq = pd.DataFrame(equity, columns=["date", "equity", "n"]).set_index("date")
    tr = pd.DataFrame(trades, columns=["symbol", "entry", "exit", "entry_px", "exit_px", "ret"])
    return eq, tr


def stats(eq: pd.Series) -> dict:
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {
        "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1,
        "max_drawdown": (eq / eq.cummax() - 1).min(),
        "sharpe": r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0.0,
    }


def equal_weight_hold(data: dict[str, pd.DataFrame], start: str, end: str | None = None) -> pd.Series:
    """Control: hold every universe stock equally (rebalanced daily), same dates."""
    closes = pd.DataFrame({s: df["Close"] for s, df in data.items() if s != "SPY"}).loc[start:end]
    rets = closes.pct_change().mean(axis=1, skipna=True).fillna(0)
    return (1 + rets).cumprod()


def scan(data: dict[str, pd.DataFrame], p: BreakoutParams, held: dict[str, float] | None = None) -> dict:
    """Today's entry candidates and exit signals for currently held symbols {symbol: entry_price}."""
    held = held or {}
    spy = data["SPY"]
    mkt = bool(market_ok(spy).iloc[-1])
    asof = spy.index[-1]
    entries, exits = [], []
    for s, df in data.items():
        if s == "SPY":
            continue
        sg = signals(df, p)
        if sg.index[-1] != asof:
            continue
        row = sg.iloc[-1]
        if s in held:
            if row.close < row.exit_low:
                exits.append((s, f"closed {row.close:.2f} below {p.exit_lookback}-day low {row.exit_low:.2f}"))
            elif row.close <= held[s] * (1 - p.stop_pct):
                exits.append((s, f"closed {row.close:.2f}, {p.stop_pct:.0%} stop from {held[s]:.2f}"))
        elif bool(row.entry) and mkt:
            vol_ratio = df["Volume"].iloc[-1] / df["Volume"].iloc[-p.vol_lookback - 1:-1].mean()
            entries.append({"symbol": s, "close": round(float(row.close), 2), "score": float(row.score),
                            "volume_x": round(float(vol_ratio), 2),
                            "stop": round(float(row.close) * (1 - p.stop_pct), 2)})
    entries.sort(key=lambda e: e["score"], reverse=True)
    return {"asof": str(asof.date()), "market_ok": mkt, "entries": entries, "exits": exits}
