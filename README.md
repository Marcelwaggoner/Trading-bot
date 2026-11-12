# Trading-bot

A mean-reversion **options** strategy with a backtester and a paper trader. **It never places real orders.**

## Strategy

- **Signal:** short-term RSI (RSI(2)) on liquid ETFs (SPY, QQQ, IWM).
  - **Buy the dip:** RSI(2) < 10 while the price is above its 200-day moving average.
  - **Fade the rip** (optional): RSI(2) > 90 while the price is below its 200-day moving average.
- **Trade:** `options.structure` in `config.toml` picks one:
  - `shares` (**default**): buy the ETF itself on dips. Long-only, fractional shares.
  - `core_dip`: hold `core_weight` of the account in SPY, rebalanced monthly, and buy dips with the rest.
  - `long_option`: buy a ~0.50-delta call (dip) or put (rip), about 30 days to expiration (options level 2).
  - `cash_secured_put`: sell a ~0.30-delta put on dips, with the cash to cover it set aside (options level 2).
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

## How option prices are modeled

Free historical options prices are hard to get, so the backtest prices options with Black-Scholes. `pricing_model` in `config.toml` chooses how.

**`calibrated` (default)** is fitted to real Robinhood quotes. [`data/calibration/options_2026-11-11.json`](data/calibration/options_2026-11-11.json) holds 29 SPY/QQQ/IWM contracts (Dec-18 expiry, 37 days out) at the 2026-11-11 close, and [`calibration.py`](trading_bot/calibration.py) fits:

- **Implied volatility level:** at-the-money IV = volatility index × a fitted ratio. That's VIX × 0.82 for SPY, VXN × 0.88 for QQQ, and VIX × 1.04 for IWM (Yahoo has no data for RVX, the Russell 2000 index).
- **Volatility skew:** IV goes up for strikes below spot and down for strikes above it, fitted separately on each side against ln(K/S)/√T. For SPY it reproduces the real smile within about 0.6 volatility points from the 710 strike to the 800 strike.
- **Bid/ask cost:** each fill pays the fitted half-spread: 0.32% of the option price for SPY, 0.80% for QQQ and 0.71% for IWM. It's scaled up by (vol index ÷ snapshot level) when volatility spikes, because spreads widen in selloffs.

**`realized`** is the original model: realized vol × 1.15 as IV, no skew, and a flat 3% cost each way. The snapshot showed it charged roughly 10× too much in trading costs, and gave SPY about 26% IV when the real figure was 14.4%.

### Checking against real prices

`scripts/validate_pricing.py` replays daily Robinhood closes for three real contracts, Aug–Nov 2026, saved in [`data/validation/`](data/validation/option_closes_2026-08_11.json):

| Contract | Calibrated: mean error, 37–60 days left | Old: mean error, 37–60 days left | Daily-change correlation (calibrated / old) |
|---|---|---|---|
| SPY 760 call | +4.0% | +11.7% | 0.957 / 0.924 |
| SPY 745 put | −1.3% | −20.7% | 0.987 / 0.938 |
| QQQ 660 put | −1.5% | −5.8% | 0.982 / 0.927 |

With more than 60 days left the model underprices by 5–30%. That's because longer-dated options carry more implied volatility than 30-day VIX shows, and the model has no adjustment for it. It doesn't affect the strategies here, which only hold options with 5–37 days left. The 37–60-day window includes the snapshot date itself, so it isn't fully out-of-sample.

**Still not modeled:**

- Early assignment and dividends.
- Interest earned on idle cash or on cash-secured-put collateral. This understates the CSP results in particular.
- How the skew and spreads change over time. Both are frozen at one day's snapshot, apart from the spread widening in selloffs.

### Options results with calibrated pricing (SPY/QQQ/IWM)

| Variant | 2016–now: CAGR / DD / Sharpe / PF | 2005–2015: CAGR / DD / Sharpe / PF |
|---|---|---|
| `long_option` $10k | +0.3% / −3% / 0.24 / 1.49 (29 trades) | +3.0% / −11% / 0.53 / 1.34 |
| `long_option` $100k | +0.5% / −28% / 0.11 / 1.06 | +2.4% / −10% / 0.48 / 1.33 |
| `long_option` $100k, calls only | +0.6% / −24% / 0.14 / 1.09 | +1.9% / −8% / 0.42 / 1.33 |
| `cash_secured_put` $100k | +0.2% / −2% / 0.21 / 1.28 | +1.0% / −4% / 0.68 / **2.02** |
| *Same rows, old `realized` pricing* | | |
| `long_option` $100k | +0.4% / −22% / 0.09 / 1.05 | +1.4% / −10% / 0.27 / 1.19 |
| `cash_secured_put` $100k | −0.2% / −4% / −0.19 / 0.80 | −0.1% / −6% / −0.04 / 0.95 |

Realistic prices mostly help the **cash-secured puts**. They go from losing money to a profit factor of 1.3–2.0 and a 71–76% win rate with small drawdowns. CAGR stays tiny because the collateral sits idle and earns 0% in the model. Buying calls and puts stays roughly break-even since 2016.

## Earlier results with the old `realized` pricing (2016-01 → 2026-11, daily)

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

### Core SPY + buying dips (`structure = "core_dip"`)

The account holds a fixed share in SPY, rebalanced monthly while no dip trade is open, and puts the remaining cash into RSI(2) dip buys. The control is the same SPY/cash split with the signal switched off, since the question is whether dip-buying beats leaving that cash parked.

| Setup | 2016–now CAGR / DD / Sharpe | 2004–2015 CAGR / DD / Sharpe |
|---|---|---|
| 80% SPY + 20% idle cash (control) | +12.0% / −27% / 0.88 | +6.1% / −47% / 0.46 |
| 80% SPY + dips on SPY (20% × 1) | +12.5% / −28% / 0.88 | +6.5% / −47% / 0.48 |
| 80% SPY + dips on SPY/QQQ/IWM (6.7% × 3) | +12.5% / −28% / 0.88 | +6.8% / −47% / 0.49 |
| 70% SPY + 30% idle cash (control) | +10.5% / −24% / 0.88 | +5.4% / −42% / 0.46 |
| 70% SPY + dips on SPY/QQQ/IWM (10% × 3) | +11.2% / −25% / 0.88 | +6.5% / −43% / 0.51 |
| **100% SPY** | **+14.9% / −34% / 0.87** | **+7.3% / −55% / 0.46** |

Dip-buying beats leaving the cash idle in both periods: about +0.5 to +1.1 points of CAGR at the same or slightly better Sharpe. It never beats holding 100% SPY on return. Choosing this setup is a decision about how much risk to take (a smaller drawdown in exchange for lower return), not a way to beat the index. Idle cash earns 0% in this model, so with interest on uninvested cash the controls would look a bit better, and the extra return from dip-buying would be smaller.

Reproduce one row with:

```bash
python -m trading_bot --set structure=core_dip --set core_weight=0.7 --set share_alloc_per_trade=0.1 backtest
```

### Ideas to try next
- Debit spreads, which would need options level 3.
- Real option-quote history in place of Black-Scholes.
- Measuring actual spreads through Robinhood.
- Out-of-sample testing before changing any thresholds.

## Scheduled paper trading

`scripts/paper_daily.sh` installs dependencies if needed, moves the ledger forward to the latest close, and appends the output to `paper/log.md`. A scheduled Claude task runs it every weekday after the market closes and commits `paper/` to this branch. That task has no Robinhood connector, so it can't place real orders.
