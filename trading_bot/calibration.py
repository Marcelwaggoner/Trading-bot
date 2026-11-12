"""Fit option-pricing parameters from a snapshot of real option quotes.

Per underlying we estimate:
  half_spread  - median (ask-bid)/2 / mid for contracts with |delta| in [0.15, 0.65]
  iv_ratio     - ATM implied vol / volatility-index level (e.g. SPY ATM IV / VIX)
  skew_down/up - relative IV change per unit of x = ln(K/S)/sqrt(T), fitted separately
                 below spot (puts) and above spot (calls), through the ATM point
The backtest then prices options with IV(K) = vol_index(t) * iv_ratio * (1 + skew * x).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class SymbolCal:
    vol_index: str
    iv_ratio: float
    skew_down: float
    skew_up: float
    half_spread: float
    index_ref: float  # vol-index level on the snapshot date (for spread stress scaling)


def fit(path: str | Path) -> dict[str, SymbolCal]:
    raw = json.loads(Path(path).read_text())
    T = (date.fromisoformat(raw["expiry"]) - date.fromisoformat(raw["date"])).days / 365
    out: dict[str, SymbolCal] = {}
    for sym, u in raw["underlyings"].items():
        S, idx_name = u["spot"], u["vol_index"]
        idx = raw["vol_index"][idx_name] / 100
        rows = [q for q in raw["quotes"] if q[0] == sym]
        # ATM IV: average call & put IV at the strike nearest spot
        k_atm = min({q[2] for q in rows}, key=lambda k: abs(k - S))
        atm = float(np.mean([q[5] for q in rows if q[2] == k_atm]))

        def slope(points: list[tuple[float, float]]) -> float:
            if not points:
                return 0.0
            x = np.array([p[0] for p in points])
            y = np.array([p[1] for p in points])
            return float((x @ y) / (x @ x))  # least squares through the origin

        down, up = [], []
        for _, kind, K, bid, ask, iv, delta in rows:
            x = math.log(K / S) / math.sqrt(T)
            rel = iv / atm - 1
            if x < -1e-3 and kind == "put":
                down.append((x, rel))
            elif x > 1e-3 and kind == "call":
                up.append((x, rel))
        spreads = [
            (ask - bid) / 2 / ((ask + bid) / 2)
            for _, _, _, bid, ask, _, delta in rows
            if 0.15 <= abs(delta) <= 0.65
        ]
        out[sym] = SymbolCal(
            vol_index=idx_name,
            iv_ratio=atm / idx,
            skew_down=slope(down),
            skew_up=slope(up),
            half_spread=float(np.median(spreads)),
            index_ref=idx,
        )
    return out


def implied_vol(cal: SymbolCal, index_level: float, S: float, K: float, T: float,
                floor: float = 0.05) -> float:
    """Skew-adjusted IV for strike K, given today's vol-index level (as a decimal)."""
    x = math.log(K / S) / math.sqrt(max(T, 1 / 365))
    m = 1 + (cal.skew_down if x < 0 else cal.skew_up) * x
    m = min(max(m, 0.5), 3.0)
    return max(index_level * cal.iv_ratio * m, floor)
