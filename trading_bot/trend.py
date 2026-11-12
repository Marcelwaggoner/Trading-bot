"""Trend-following portfolio backtester (monthly rebalanced, long-only, cash earns T-bills).

Rules (signal on the last trading day of each month, filled at the NEXT day's close):
  equal     - always hold every symbol equally weighted (the control: same universe, no timing)
  sma       - hold each symbol at 1/N weight only while its close > its N-day SMA, else cash
  momentum  - rank by trailing total return over `lookback` days, hold the top `top` equally,
              each only if its own trailing return beats cash (absolute momentum), else cash
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import load_bars

CASH = "^IRX"  # 13-week T-bill yield, percent


@dataclass
class TrendParams:
    rule: str = "sma"
    sma: int = 200
    lookback: int = 252
    top: int = 3
    cost_bps: float = 5.0  # per side, on traded notional


def load_closes(symbols: list[str], start: str, refresh: bool = False) -> tuple[pd.DataFrame, pd.Series]:
    closes = pd.DataFrame({s: load_bars(s, start, refresh)["Close"] for s in symbols}).dropna()
    irx = load_bars(CASH, start, refresh)["Close"].reindex(closes.index).ffill().fillna(0.0)
    cash_daily = irx / 100 / 252  # simple daily accrual of the annualized T-bill yield
    return closes, cash_daily


def target_weights(closes: pd.DataFrame, cash_daily: pd.Series, p: TrendParams) -> pd.DataFrame:
    """Target weights on each month-end signal date (rows), symbols as columns. Remainder is cash."""
    month_end = closes.groupby(closes.index.to_period("M")).tail(1).index
    n = closes.shape[1]
    if p.rule == "equal":
        w = pd.DataFrame(1.0 / n, index=closes.index, columns=closes.columns)
    elif p.rule == "sma":
        sma = closes.rolling(p.sma, min_periods=p.sma).mean()
        w = (closes > sma).astype(float) / n
        w[sma.isna()] = np.nan
    elif p.rule == "momentum":
        mom = closes / closes.shift(p.lookback) - 1
        cash_mom = (1 + cash_daily).rolling(p.lookback).apply(np.prod, raw=True) - 1
        rank = mom.rank(axis=1, ascending=False)
        keep = (rank <= p.top) & mom.gt(cash_mom, axis=0)
        w = keep.astype(float) / min(p.top, n)
        w[mom.isna()] = np.nan
    else:
        raise ValueError(f"unknown rule {p.rule!r}")
    return w.loc[month_end].dropna(how="any")


def simulate(
    closes: pd.DataFrame, cash_daily: pd.Series, targets: pd.DataFrame, p: TrendParams,
    start_cash: float = 10_000.0,
) -> tuple[pd.Series, pd.Series, float]:
    """Daily mark-to-market with drift between rebalances. Returns equity, invested fraction, turnover/yr."""
    rets = closes.pct_change().fillna(0.0)
    dates = closes.index
    # signal on date t -> trade at close of t+1
    exec_on = {dates[dates.get_loc(t) + 1]: t for t in targets.index if dates.get_loc(t) + 1 < len(dates)}
    first = min(exec_on) if exec_on else None
    vals = pd.Series(0.0, index=closes.columns)
    cash = start_cash
    eq, inv = [], []
    traded = 0.0
    started = False
    for d in dates:
        if first is None or d < first:
            continue
        if started:
            vals = vals * (1 + rets.loc[d])
            cash *= 1 + cash_daily.loc[d]
        if d in exec_on:
            total = cash + vals.sum()
            w = targets.loc[exec_on[d]]
            trade = (w * total - vals).abs().sum()
            cost = trade * p.cost_bps / 1e4
            traded += trade / total
            new_vals = w * (total - cost)  # size after costs so cash never goes negative
            cash = total - cost - new_vals.sum()
            vals = new_vals
            started = True
        total = cash + vals.sum()
        eq.append((d, total))
        inv.append((d, vals.sum() / total if total else 0.0))
    equity = pd.Series(dict(eq), name="equity")
    invested = pd.Series(dict(inv), name="invested")
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9) if len(equity) else 1
    return equity, invested, traded / years


def stats(equity: pd.Series) -> dict:
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    r = equity.pct_change().dropna()
    downside = r[r < 0].std() * math.sqrt(252)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    return {
        "cagr": cagr,
        "max_drawdown": (equity / equity.cummax() - 1).min(),
        "sharpe": r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else 0.0,
        "sortino": r.mean() * 252 / downside if downside > 0 else 0.0,
        "vol": r.std() * math.sqrt(252),
        "worst_year": equity.resample("YE").last().pct_change().min()
        if len(equity.resample("YE").last()) > 2 else float("nan"),
    }


def run(symbols: list[str], p: TrendParams, start: str, end: str | None = None, refresh: bool = False):
    warm = str(int(start[:4]) - 2) + start[4:]  # history for SMA / lookback warm-up
    closes, cash_daily = load_closes(symbols, warm, refresh)
    targets = target_weights(closes, cash_daily, p)
    targets = targets[targets.index >= pd.Timestamp(start) - pd.offsets.MonthEnd(1)]
    if end:
        closes, cash_daily = closes.loc[:end], cash_daily.loc[:end]
        targets = targets.loc[:end]
    equity, invested, turnover = simulate(closes, cash_daily, targets, p)
    m = stats(equity)
    m["avg_invested"] = invested.mean()
    m["turnover_per_year"] = turnover
    m["start"], m["end"] = str(equity.index[0].date()), str(equity.index[-1].date())
    return equity, targets, m
