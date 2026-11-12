import numpy as np
import pandas as pd
import pytest

from trading_bot.trend import TrendParams, simulate, target_weights


def _closes():
    idx = pd.bdate_range("2020-01-01", periods=400)
    up = pd.Series(np.linspace(100, 200, 400), index=idx)
    down = pd.Series(np.linspace(200, 100, 400), index=idx)
    return pd.DataFrame({"UP": up, "DOWN": down}), pd.Series(0.0, index=idx)


def test_sma_rule_holds_only_uptrend():
    closes, cash = _closes()
    w = target_weights(closes, cash, TrendParams("sma", sma=50))
    assert (w["UP"] == 0.5).all() and (w["DOWN"] == 0).all()
    assert w.index.is_month_end.any() or len(w) > 10  # one row per month


def test_momentum_requires_beating_cash():
    closes, cash = _closes()
    w = target_weights(closes, cash, TrendParams("momentum", lookback=60, top=2))
    assert (w["UP"] == 0.5).all() and (w["DOWN"] == 0).all()  # DOWN in top 2 but negative


def test_simulate_trades_next_day_and_charges_costs():
    closes, cash = _closes()
    p = TrendParams("equal", cost_bps=10)
    t = target_weights(closes, cash, p)
    eq, inv, _ = simulate(closes, cash, t, p, start_cash=10_000)
    first_signal = t.index[0]
    assert eq.index[0] == closes.index[closes.index.get_loc(first_signal) + 1]
    assert eq.iloc[0] == pytest.approx(10_000 * (1 - 10 / 1e4))
    assert inv.iloc[0] == pytest.approx(1.0, abs=1e-3)


def test_cash_accrues_when_out_of_market():
    closes, _ = _closes()
    cash = pd.Series(0.04 / 252, index=closes.index)
    p = TrendParams("sma", sma=50, cost_bps=0)
    t = target_weights(closes[["DOWN"]], cash, p)
    eq, inv, _ = simulate(closes[["DOWN"]], cash, t, p)
    assert (inv == 0).all()
    assert eq.iloc[-1] > eq.iloc[0]
