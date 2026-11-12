# Trading-bot

A mean-reversion **options** strategy with a backtester and a paper trader. **It never places real orders.**

## Strategy

- **Signal:** short-term RSI (RSI(2)) on liquid ETFs (SPY, QQQ, IWM).
  - **Buy the dip:** RSI(2) < 10 while the price is above its 200-day moving average.
  - **Fade the rip** (optional): RSI(2) > 90 while the price is below its 200-day moving average.
- **Trade:** limited to Robinhood options level 2. `options.structure` in `config.toml` picks one:
  - `long_option`: buy a ~0.50-delta call (dip) or put (rip), about 30 days to expiration.
  - `cash_secured_put`: sell a ~0.30-delta put on dips, with the cash to cover it set aside.
- **Exits** (whichever comes first):
  - RSI moves back to normal.
  - Profit target or stop-loss hit.
  - 10-day time stop.
  - 5 or fewer days left to expiration.
- **Timing:** decisions use day *t*'s close and fill at day *t+1*'s close, so there's no look-ahead.

All settings are in [`config.toml`](config.toml).

## Usage

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Backtest (downloads daily bars from Yahoo Finance into data/cache/)
.venv/bin/python -m trading_bot backtest --start 2016-01-01
.venv/bin/python -m trading_bot backtest --structure cash_secured_put -v   # -v prints every signal and fill

# Paper trade: run once per trading day after the close
.venv/bin/python -m trading_bot paper

# Tests
.venv/bin/python -m pytest -q
```

The backtester writes `results/equity.csv` and `results/trades.csv`. The paper trader keeps its state in `paper/ledger.json` and replays any days it missed.

### Marking the paper trades with real Robinhood prices

Pass `--quotes quotes.json`, where each key is a position label (as printed by the paper trader) and each value is the real mid price per share:

```json
{"SPY 2026-12-18 760 C": 14.35}
```

Claude can fill this in for you from the Robinhood connector with `get_option_chains`, `get_option_instruments` and `get_option_quotes`.

## How the backtest works (read before trusting results)

Free historical options prices are hard to get, so the backtest **prices options with Black-Scholes**:

- **Implied volatility:** 20-day realized volatility × 1.15. Real implied volatility usually runs higher than realized volatility, and jumps after selloffs, which is exactly when this strategy buys. The model misses that.
- **Spread cost:** 3% of the premium on each side, at least $0.01.
- **Not modeled:**
  - Volatility skew.
  - Early assignment.
  - Dividends.
- **Expiration:** an option still open at expiration settles at its intrinsic value.

Treat the results as a check on direction and order of magnitude, not as a P&L forecast.

## Results so far (2016-01 → 2026-11, daily)

| Variant | CAGR | Max DD | Sharpe | Trades | Profit factor |
|---|---|---|---|---|---|
| `long_option`, $10k, 2% risk (default) | −0.1% | −4% | −0.06 | 24 | 0.87 |
| `long_option`, $10k, 10% risk | −8.7% | −63% | −0.42 | 107 | 0.57 |
| `long_option`, $100k, 2% risk | +0.4% | −22% | 0.09 | 328 | 1.05 |
| `long_option`, $100k, 2% risk, **zero costs** | +3.2% | −19% | 0.53 | 337 | 1.45 |
| `cash_secured_put`, $100k | −0.2% | −4% | −0.19 | 126 | 0.80 |
| Buy & hold SPY | +14.9% | −34% | — | — | — |

What this shows:

1. **The signal is real but small.** With zero trading costs, profit factor is 1.45. A 3% bid/ask cost on each side takes almost all of it, because a 30-day option loses value to time decay and the spread on both entry and exit.
2. **$10k is too small for these ETFs.** One at-the-money SPY contract costs more than $1,000, so at 2% risk 606 of 630 signals were skipped. Raising the risk per trade to compensate blew up the account.
3. **Cash-secured puts need about $70k of collateral per SPY contract**, so with $10k they can't trade at all.

### Cheaper underlyings ($10k, up to 5 positions)

ETFs: XLF, EEM, GDX, SLV, KRE, XLE, EWZ, FXI, ARKK, XLU. Stocks: F, BAC, T, INTC, PFE, CCL, AAL, VZ. Both groups were chosen from tickers that still exist today, so these numbers are biased upward (survivorship bias), most of all for the stocks.

| Universe / structure | CAGR (3% costs) | Max DD | Profit factor | CAGR (zero costs) | PF (zero costs) |
|---|---|---|---|---|---|
| ETFs, `long_option` | −7.5% | −61% | 0.65 | −3.0% | 0.89 |
| ETFs, `cash_secured_put` | +0.1% | −4% | 1.07 | +0.6% | 1.42 |
| Stocks, `long_option` | −6.4% | −55% | 0.73 | −1.8% | 0.94 |
| Stocks, `cash_secured_put` | −0.2% | −6% | 0.91 | +0.6% | 1.28 |

Cheaper underlyings don't help. Long options lose money on these names **even with zero costs**, so the RSI(2) edge is concentrated in broad index ETFs. Cash-secured puts barely break even, because the cash they hold back sits idle most of the time.

Reproduce one row with:

```bash
python -m trading_bot --set symbols=XLF,EEM,GDX,SLV,KRE,XLE,EWZ,FXI,ARKK,XLU \
  --set max_positions=5 --set structure=cash_secured_put backtest
```

### The signal on plain shares (`structure = "shares"`)

This tests the signal without options: buy the ETF itself (long-only, fractional shares), with the same entries and RSI/time exits and 5 bps of slippage per side. Exposure is the share of days with any position open.

| Universe / period | CAGR | Max DD | Sharpe | Trades | Win rate | Profit factor | Exposure | SPY B&H CAGR / DD / Sharpe |
|---|---|---|---|---|---|---|---|---|
| SPY/QQQ/IWM 2016–now | +2.3% | −13% | 0.41 | 256 | 67% | 1.52 | 22% | +14.9% / −34% / 0.87 |
| …same, zero costs | +3.1% | −12% | 0.54 | 256 | 69% | 1.74 | 22% | |
| SPY/QQQ/IWM 2016–2020 | −1.5% | −13% | −0.20 | 101 | 58% | 0.74 | 21% | +15.5% / −34% / 0.86 |
| SPY/QQQ/IWM 2021–now | +5.4% | −5% | 0.97 | 154 | 72% | 2.25 | 23% | +14.6% / −25% / 0.90 |
| SPY/QQQ/IWM 2004–2015 | +3.5% | −16% | 0.58 | 301 | 67% | 1.72 | 22% | +7.3% / −55% / 0.46 |
| 10 cheap ETFs 2016–now | −1.5% | −34% | −0.13 | 684 | 60% | 0.90 | 52% | |
| 8 stocks 2016–now | −1.9% | −22% | −0.24 | 504 | 56% | 0.86 | 49% | |

What this shows:

- **The edge is real on broad index ETFs.** It made money in 2004–2015, before the stretch I'd been testing, so it isn't just fit to that period. In 2004–2015 its Sharpe ratio beat buy & hold, with less than a third of the drawdown.
- **It's inconsistent.** It lost money from 2016 to 2020.
- **It's small in absolute terms.** It's invested only about 22% of the time, so its CAGR is a fraction of buy & hold. Idle cash earns nothing in this model, so with a cash yield (for example interest on uninvested cash) the real number would be somewhat higher.
- **It doesn't work on sector, commodity or country ETFs, or on single stocks.**
- **Options made it worse.** Wrapping the signal in options turned a profit factor of about 1.5 into roughly 1.0 or below, because of spreads and time decay. If you trade this, trade the shares.

### Ideas to try next
- Debit spreads, which would need options level 3.
- Real option-quote history in place of Black-Scholes.
- Measuring actual spreads through Robinhood.
- Out-of-sample testing before changing any thresholds.
