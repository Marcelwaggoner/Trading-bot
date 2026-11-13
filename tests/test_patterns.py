import pandas as pd

from trading_bot.patterns import detect


def _bars(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)


def _pad(n=25):
    return [(100, 101, 99, 100.5) if i % 2 else (100.5, 101, 99, 100) for i in range(n)]


def test_bullish_engulfing():
    df = _bars(_pad() + [(105, 105.5, 101, 101.5), (101, 107, 100.5, 106)])
    assert detect(df)["bullish_engulfing"].iloc[-1]


def test_three_white_soldiers():
    df = _bars(_pad() + [(100, 103.2, 99.8, 103), (102.5, 106.2, 102.3, 106), (105.5, 109.2, 105.3, 109)])
    s = detect(df)
    assert s["three_white_soldiers"].iloc[-1] and not s["three_white_soldiers"].iloc[-2]
