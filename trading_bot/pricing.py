"""Black-Scholes pricing used to simulate option fills when real quotes are unavailable."""
from __future__ import annotations

import math
from datetime import date, timedelta


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(S: float, K: float, T: float, r: float, sigma: float) -> tuple[float, float]:
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return d1, d1 - sigma * math.sqrt(T)


def intrinsic(S: float, K: float, kind: str) -> float:
    return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> float:
    if T <= 0 or sigma <= 0:
        return intrinsic(S, K, kind)
    d1, d2 = _d1_d2(S, K, T, r, sigma)
    if kind == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, sigma: float, kind: str) -> float:
    if T <= 0 or sigma <= 0:
        itm = S > K if kind == "call" else S < K
        return (1.0 if itm else 0.0) * (1 if kind == "call" else -1)
    d1, _ = _d1_d2(S, K, T, r, sigma)
    return _norm_cdf(d1) if kind == "call" else _norm_cdf(d1) - 1.0


def strike_for_delta(
    S: float, T: float, r: float, sigma, kind: str, target: float, increment: float
) -> float:
    """Strike on the `increment` grid whose |delta| is closest to `target`.

    `sigma` is a float or a callable K -> IV (to respect the volatility skew)."""
    sig = sigma if callable(sigma) else (lambda _k: sigma)
    center = round(S / increment) * increment
    span = max(int(S * 0.5 / increment), 5)
    best_k, best_err = center, float("inf")
    for i in range(-span, span + 1):
        k = round(center + i * increment, 4)
        if k <= 0:
            continue
        err = abs(abs(bs_delta(S, k, T, r, sig(k), kind)) - target)
        if err < best_err:
            best_k, best_err = k, err
    return best_k


def expiry_for_dte(today: date, target_dte: int) -> date:
    """First Friday on or after today + target_dte (standard weekly expirations)."""
    d = today + timedelta(days=target_dte)
    return d + timedelta(days=(4 - d.weekday()) % 7)


def year_frac(today: date, expiry: date) -> float:
    return max((expiry - today).days, 0) / 365.0
