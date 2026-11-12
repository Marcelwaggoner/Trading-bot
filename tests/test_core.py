import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from trading_bot.config import Config
from trading_bot.engine import Bar, Engine
from trading_bot.indicators import rsi
from trading_bot.pricing import bs_delta, bs_price, expiry_for_dte, strike_for_delta


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 50, dtype=float))
    assert rsi(up, 2).dropna().eq(100).all()
    down = pd.Series(np.arange(50, 1, -1, dtype=float))
    assert rsi(down, 2).dropna().eq(0).all()
    noise = pd.Series(100 + np.random.default_rng(0).normal(size=300).cumsum())
    r = rsi(noise, 14).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_put_call_parity():
    S, K, T, r, s = 100.0, 105.0, 0.25, 0.04, 0.2
    c = bs_price(S, K, T, r, s, "call")
    p = bs_price(S, K, T, r, s, "put")
    assert c - p == pytest.approx(S - K * math.exp(-r * T), abs=1e-9)


def test_delta_and_strike_selection():
    S, T, r, s = 500.0, 30 / 365, 0.04, 0.18
    k = strike_for_delta(S, T, r, s, "call", 0.5, 1.0)
    assert abs(k - S) <= 5
    kp = strike_for_delta(S, T, r, s, "put", 0.30, 1.0)
    assert kp < S
    assert abs(bs_delta(S, kp, T, r, s, "put")) == pytest.approx(0.30, abs=0.03)


def test_expiry_is_friday():
    for i in range(7):
        e = expiry_for_dte(date(2026, 1, 1) + timedelta(days=i), 30)
        assert e.weekday() == 4


def _cfg(**kw):
    base = dict(symbols=["XYZ"], starting_cash=100_000, max_positions=1)
    base.update(kw)
    return Config(**base)


def test_signal_fills_next_day_not_same_day():
    eng = Engine(_cfg())
    d0 = date(2026, 3, 2)
    eng.step(d0, {"XYZ": Bar(close=100, rsi=5, sma=90, vol=0.2)})
    assert eng.pf.positions == [] and len(eng.pf.pending) == 1  # decided, not filled
    eng.step(d0 + timedelta(days=1), {"XYZ": Bar(close=101, rsi=30, sma=90, vol=0.2)})
    assert len(eng.pf.positions) == 1
    p = eng.pf.positions[0]
    assert p.kind == "call" and p.side == "long" and p.entry_date == d0 + timedelta(days=1)


def test_no_long_entry_below_trend():
    eng = Engine(_cfg(allow_bearish=False))
    eng.step(date(2026, 3, 2), {"XYZ": Bar(close=80, rsi=5, sma=90, vol=0.2)})
    assert eng.pf.pending == []


def test_round_trip_cash_accounting():
    cfg = _cfg(slippage_pct=0.0, min_slippage=0.0, fee_per_contract=0.0)
    eng = Engine(cfg)
    d = date(2026, 3, 2)
    bar = Bar(close=100, rsi=5, sma=90, vol=0.2)
    eng.step(d, {"XYZ": bar})
    eng.step(d + timedelta(days=1), {"XYZ": bar})
    # RSI reverts -> exit signal, filled the following day at an unchanged model price
    exit_bar = Bar(close=100, rsi=80, sma=90, vol=0.2)
    eng.step(d + timedelta(days=2), {"XYZ": exit_bar})
    eng.step(d + timedelta(days=3), {"XYZ": exit_bar})
    assert eng.pf.positions == []
    t = eng.pf.trades[0]
    assert t.reason == "signal_exit"
    assert eng.pf.cash == pytest.approx(cfg.starting_cash + t.pnl)


def test_cash_secured_put_reserves_collateral():
    cfg = _cfg(structure="cash_secured_put")
    eng = Engine(cfg)
    d = date(2026, 3, 2)
    bar = Bar(close=100, rsi=5, sma=90, vol=0.2)
    eng.step(d, {"XYZ": bar})
    eng.step(d + timedelta(days=1), {"XYZ": bar})
    p = eng.pf.positions[0]
    assert p.side == "short" and p.kind == "put" and p.strike < 100
    assert p.collateral <= cfg.starting_cash * cfg.csp_alloc_per_trade
    assert eng.pf.buying_power() == pytest.approx(eng.pf.cash - p.collateral)


def test_expired_option_settles_at_intrinsic():
    cfg = _cfg(slippage_pct=0.0, min_slippage=0.0, fee_per_contract=0.0, min_dte_exit=-1,
               max_hold_days=999, profit_target=99, stop_loss=99, rsi_exit_long=101)
    eng = Engine(cfg)
    d = date(2026, 3, 2)
    eng.step(d, {"XYZ": Bar(close=100, rsi=5, sma=90, vol=0.2)})
    eng.step(d + timedelta(days=1), {"XYZ": Bar(close=100, rsi=50, sma=90, vol=0.2)})
    p = eng.pf.positions[0]
    eng.step(p.expiry, {"XYZ": Bar(close=p.strike + 7, rsi=50, sma=90, vol=0.2)})
    t = eng.pf.trades[0]
    assert t.reason == "expired" and t.exit_price == pytest.approx(7)
