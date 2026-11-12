from datetime import date, timedelta

import pytest

from trading_bot.calibration import fit, implied_vol
from trading_bot.config import Config
from trading_bot.engine import Bar, Engine

CAL = "data/calibration/options_2026-11-11.json"


def test_fit_reproduces_snapshot_smile():
    spy = fit(CAL)["SPY"]
    T = 37 / 365
    for K, real in [(710, 0.208), (745, 0.165), (760, 0.144), (790, 0.116)]:
        assert implied_vol(spy, 0.1749, 759.81, K, T) == pytest.approx(real, abs=0.008)
    assert spy.skew_down < 0 and spy.half_spread < 0.01


def test_engine_uses_vol_index_and_real_spreads():
    cfg = Config(symbols=["SPY"], starting_cash=100_000, max_positions=1, calibration_file=CAL)
    eng = Engine(cfg)
    d = date(2026, 3, 2)
    bar = Bar(close=760, rsi=5, sma=700, vol=0.40, ivx=0.1749)  # realized vol deliberately high
    eng.step(d, {"SPY": bar})
    eng.step(d + timedelta(days=1), {"SPY": bar})
    p = eng.pf.positions[0]
    T = (p.expiry - p.entry_date).days / 365
    iv = eng._iv(bar, "SPY", p.strike, T)
    assert iv == pytest.approx(0.144, abs=0.01)  # from VIX, not the 40% realized vol
    slip_pct = (p.entry_price - p.mark) / p.mark
    assert slip_pct == pytest.approx(fit(CAL)["SPY"].half_spread, rel=0.05)


def test_spreads_widen_when_vol_index_spikes():
    cfg = Config(symbols=["SPY"], calibration_file=CAL)
    eng = Engine(cfg)
    eng._bars = {"SPY": Bar(close=760, rsi=50, sma=700, vol=0.2, ivx=0.35)}
    calm = Engine(cfg)
    calm._bars = {"SPY": Bar(close=760, rsi=50, sma=700, vol=0.2, ivx=0.1749)}
    assert eng._slip(10.0, "call", "SPY") == pytest.approx(2 * calm._slip(10.0, "call", "SPY"), rel=0.01)
