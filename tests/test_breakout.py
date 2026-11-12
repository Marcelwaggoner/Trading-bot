import numpy as np
import pandas as pd

from trading_bot.breakout import BreakoutParams, backtest, scan, signals


def _df(close, vol):
    idx = pd.bdate_range("2020-01-01", periods=len(close))
    c = pd.Series(close, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": pd.Series(vol, index=idx, dtype=float)})


def _universe():
    n = 400
    flat = np.r_[np.linspace(100, 110, n - 1), 130.0]       # breaks out on the last day
    vol = np.r_[np.full(n - 1, 1e6), 3e6]
    spy = _df(np.linspace(300, 400, n), np.full(n, 1e7))
    return {"BRK": _df(flat, vol), "SPY": spy}


def test_breakout_requires_new_high_and_volume():
    d = _universe()
    sg = signals(d["BRK"], BreakoutParams())
    assert bool(sg["entry"].iloc[-1]) and not sg["entry"].iloc[-2]


def test_scan_reports_entry_and_exit():
    d = _universe()
    res = scan(d, BreakoutParams())
    assert res["market_ok"] and res["entries"][0]["symbol"] == "BRK"
    res2 = scan(d, BreakoutParams(), held={"BRK": 200.0})  # 130 is >10% below 200 -> stop
    assert res2["exits"] and res2["exits"][0][0] == "BRK"


def test_backtest_buys_next_open_after_signal():
    d = _universe()
    n = 400
    ext = np.r_[np.linspace(100, 110, n - 1), 130.0, 131, 132]
    d["BRK"] = _df(ext, np.r_[np.full(n - 1, 1e6), 3e6, 1e6, 1e6])
    d["SPY"] = _df(np.linspace(300, 400, n + 2), np.full(n + 2, 1e7))
    eq, tr = backtest(d, BreakoutParams(max_positions=1, cost_bps=0), "2020-06-01")
    assert eq["n"].iloc[-1] == 1 and eq["n"].iloc[-3] == 0
