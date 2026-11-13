"""Candlestick and chart-pattern detectors on OHLC bars, plus a forward-return study.

Every detector returns a boolean Series that is True on the bar where the pattern
COMPLETES (known at that bar's close). The study measures returns from the NEXT
bar's open, so there is no look-ahead.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _parts(df: pd.DataFrame):
    o, h, l, c = (df[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    body = c - o
    rng = (h - l).replace(0, np.nan)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    avg_body = body.abs().rolling(20).mean()
    return o, h, l, c, body, rng, upper, lower, avg_body


def detect(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c, body, rng, upper, lower, avg_body = _parts(df)
    ab = body.abs()
    green, red = body > 0, body < 0
    down_trend = c.shift(1) < c.shift(6)   # prior 5 bars falling
    up_trend = c.shift(1) > c.shift(6)
    big = ab > avg_body                    # larger than the recent average body
    small = ab < 0.3 * avg_body
    doji = ab <= 0.1 * rng
    hammer_shape = (lower >= 2 * ab) & (upper <= 0.5 * ab.clip(lower=1e-12) + 0.1 * rng)
    inv_hammer_shape = (upper >= 2 * ab) & (lower <= 0.5 * ab.clip(lower=1e-12) + 0.1 * rng)
    p_o, p_c, p_body = o.shift(1), c.shift(1), body.shift(1)
    mid2 = (o.shift(2) + c.shift(2)) / 2

    pats = {
        # bullish
        "hammer": hammer_shape & down_trend,
        "inverted_hammer": inv_hammer_shape & down_trend,
        "bullish_engulfing": (p_body < 0) & green & (o <= p_c) & (c >= p_o),
        "bullish_harami": (p_body < 0) & big.shift(1) & green & (o > p_c) & (c < p_o),
        "piercing_line": (p_body < 0) & big.shift(1) & green & (o < p_c) & (c > (p_o + p_c) / 2) & (c < p_o),
        "morning_star": (body.shift(2) < 0) & big.shift(2) & small.shift(1) & green & (c > mid2),
        "three_white_soldiers": green & green.shift(1) & green.shift(2)
        & (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & big & big.shift(1) & big.shift(2),
        "dragonfly_doji": doji & (upper <= 0.1 * rng) & down_trend,
        "bullish_marubozu": green & (ab >= 0.9 * rng) & big,
        "tweezer_bottom": (p_body < 0) & green & ((l - l.shift(1)).abs() <= 0.001 * c) & down_trend,
        # bearish
        "shooting_star": inv_hammer_shape & up_trend,
        "hanging_man": hammer_shape & up_trend,
        "bearish_engulfing": (p_body > 0) & red & (o >= p_c) & (c <= p_o),
        "bearish_harami": (p_body > 0) & big.shift(1) & red & (o < p_c) & (c > p_o),
        "dark_cloud_cover": (p_body > 0) & big.shift(1) & red & (o > p_c) & (c < (p_o + p_c) / 2) & (c > p_o),
        "evening_star": (body.shift(2) > 0) & big.shift(2) & small.shift(1) & red & (c < mid2),
        "three_black_crows": red & red.shift(1) & red.shift(2)
        & (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & big & big.shift(1) & big.shift(2),
        "gravestone_doji": doji & (lower <= 0.1 * rng) & up_trend,
        "bearish_marubozu": red & (ab >= 0.9 * rng) & big,
        "tweezer_top": (p_body > 0) & red & ((h - h.shift(1)).abs() <= 0.001 * c) & up_trend,
    }
    # Chart patterns (triangles / wedges / rectangles / flags all share one mechanic:
    # a contracting range that then breaks). Squeeze = 20-bar range in the tightest
    # 20% of the past year; breakout = close beyond that range.
    width = (h.rolling(20).max() - l.rolling(20).min()) / c
    squeeze = width.shift(1) <= width.shift(1).rolling(365, min_periods=120).quantile(0.2)
    pats["squeeze_breakout_up"] = squeeze & (c > h.shift(1).rolling(20).max())
    pats["squeeze_breakout_down"] = squeeze & (c < l.shift(1).rolling(20).min())
    return pd.DataFrame(pats).fillna(False).astype(bool)


BULLISH = {"hammer", "inverted_hammer", "bullish_engulfing", "bullish_harami", "piercing_line",
           "morning_star", "three_white_soldiers", "dragonfly_doji", "bullish_marubozu",
           "tweezer_bottom", "squeeze_breakout_up"}


def study(df: pd.DataFrame, horizons=(1, 5, 10), start=None, end=None) -> pd.DataFrame:
    """Forward return from next open to close N bars later, per pattern, vs all bars."""
    sig = detect(df).loc[start:end]
    o, c = df["Open"].astype(float), df["Close"].astype(float)
    rows = []
    for n in horizons:
        fwd = (c.shift(-n) / o.shift(-1) - 1).loc[start:end]
        base = fwd.dropna()
        for name in sig.columns:
            x = fwd[sig[name]].dropna()
            if len(x) < 5:
                rows.append((name, n, len(x), np.nan, np.nan, np.nan))
                continue
            sign = 1 if name in BULLISH else -1
            edge = sign * (x.mean() - base.mean())          # >0 means pattern worked as advertised
            t = edge / (x.std(ddof=1) / math.sqrt(len(x)))
            hit = (sign * x > 0).mean()
            rows.append((name, n, len(x), edge, t, hit))
    return pd.DataFrame(rows, columns=["pattern", "days", "count", "edge_vs_avg", "t_stat", "hit_rate"])
