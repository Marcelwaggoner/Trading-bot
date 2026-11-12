"""Compare model option prices against real daily closes (data/validation/*.json)."""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trading_bot.calibration import fit, implied_vol  # noqa: E402
from trading_bot.config import Config  # noqa: E402
from trading_bot.data import load_bars  # noqa: E402
from trading_bot.indicators import realized_vol  # noqa: E402
from trading_bot.pricing import bs_price  # noqa: E402

cfg = Config.load("config.toml")
cal = fit(cfg.calibration_file)
val = json.loads(Path(sys.argv[1] if len(sys.argv) > 1 else
                      "data/validation/option_closes_2026-08_11.json").read_text())
dates = pd.to_datetime(val["dates"])
expiry = date(2026, 12, 18)
for name, c in val["contracts"].items():
    sym = c["symbol"]
    px = load_bars(sym, "2025-01-01", refresh=True)["Close"]
    rv = realized_vol(px, cfg.vol_window)
    idx = load_bars(cal[sym].vol_index, "2025-01-01", refresh=True)["Close"] / 100
    rows = []
    for d, real in zip(dates, c["closes"]):
        S, T = float(px.loc[d]), (expiry - d.date()).days / 365
        iv_new = implied_vol(cal[sym], float(idx.loc[d]), S, c["strike"], T)
        iv_old = max(float(rv.loc[d]) * cfg.iv_multiplier, cfg.iv_floor)
        rows.append((real, bs_price(S, c["strike"], T, cfg.risk_free_rate, iv_new, c["kind"]),
                     bs_price(S, c["strike"], T, cfg.risk_free_rate, iv_old, c["kind"])))
    a = np.array(rows)
    err_new, err_old = (a[:, 1] - a[:, 0]) / a[:, 0], (a[:, 2] - a[:, 0]) / a[:, 0]
    # daily changes: does the model capture the day-to-day P&L of holding the option?
    dn = np.corrcoef(np.diff(a[:, 0]), np.diff(a[:, 1]))[0, 1]
    do = np.corrcoef(np.diff(a[:, 0]), np.diff(a[:, 2]))[0, 1]
    print(f"{name:13}  calibrated: mean err {err_new.mean():+6.1%}  mean |err| {np.abs(err_new).mean():5.1%}"
          f"  daily-change corr {dn:.3f}   |   old realized-vol: mean err {err_old.mean():+6.1%}"
          f"  mean |err| {np.abs(err_old).mean():5.1%}  corr {do:.3f}")

    dte = np.array([(expiry - d.date()).days for d in dates])
    for lo, hi in [(90, 140), (60, 90), (37, 60)]:
        m = (dte >= lo) & (dte < hi)
        if m.any():
            print(f"    DTE {lo:3}-{hi:3}: calibrated mean err {err_new[m].mean():+6.1%}   old {err_old[m].mean():+6.1%}")
