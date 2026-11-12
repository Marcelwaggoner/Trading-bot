from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import Config
from .indicators import realized_vol, rsi, sma

CACHE_DIR = Path("data/cache")


def load_bars(symbol: str, start: str = "2010-01-01", refresh: bool = False) -> pd.DataFrame:
    """Daily OHLCV (split/dividend adjusted) from Yahoo Finance, cached as CSV."""
    path = CACHE_DIR / f"{symbol}.csv"
    if path.exists() and not refresh:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if not df.empty and str(df.index[0].date()) <= start:
            return df
    import yfinance as yf

    df = yf.download(symbol, start=start, progress=False, auto_adjust=True)
    if df.empty:
        raise RuntimeError(f"no data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df.index.name = "Date"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def features(bars: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    close = bars["Close"].astype(float)
    out = pd.DataFrame(
        {
            "close": close,
            "rsi": rsi(close, cfg.rsi_period),
            "sma": sma(close, cfg.trend_sma),
            "vol": realized_vol(close, cfg.vol_window),
        }
    )
    return out.dropna()
