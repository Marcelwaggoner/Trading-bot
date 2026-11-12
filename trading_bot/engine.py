"""Strategy + portfolio logic shared by the backtester and the paper trader.

Timing (no look-ahead): decisions are made from day t's close and filled at
day t+1's close. Expirations settle immediately at intrinsic value.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

from .config import Config
from .pricing import bs_price, expiry_for_dte, intrinsic, strike_for_delta, year_frac


@dataclass
class Bar:
    close: float
    rsi: float
    sma: float
    vol: float


@dataclass
class Position:
    symbol: str
    kind: str          # "call" | "put" | "stock"
    side: str          # "long" | "short"
    strike: float
    expiry: date
    qty: float          # contracts for options, (fractional) shares for stock
    entry_date: date
    entry_price: float  # per-share price/premium actually paid/received
    direction: str      # "bullish" | "bearish" (the mean-reversion bet) | "core" (buy-and-hold sleeve)
    mark: float = 0.0

    @property
    def multiplier(self) -> int:
        return 1 if self.kind == "stock" else 100

    @property
    def label(self) -> str:
        if self.kind == "stock":
            return f"{self.symbol} {'core ' if self.direction == 'core' else ''}shares"
        return f"{self.symbol} {self.expiry.isoformat()} {self.strike:g} {self.kind[0].upper()}"

    @property
    def collateral(self) -> float:
        return self.strike * 100 * self.qty if self.side == "short" else 0.0

    def value(self) -> float:
        sign = 1 if self.side == "long" else -1
        return sign * self.mark * self.multiplier * self.qty

    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.side == "long":
            return (self.mark - self.entry_price) / self.entry_price
        return (self.entry_price - self.mark) / self.entry_price


@dataclass
class Trade:
    symbol: str
    label: str
    side: str
    direction: str
    qty: float
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    pnl: float
    reason: str


@dataclass
class Order:
    action: str          # "open" | "close"
    symbol: str
    direction: str = ""  # for open
    reason: str = ""     # for close


@dataclass
class Portfolio:
    cash: float
    positions: list[Position] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    pending: list[Order] = field(default_factory=list)
    last_rebalance: str = ""  # "YYYY-MM" of the last core rebalance (core_dip only)

    def trading_positions(self) -> list[Position]:
        """Signal-driven positions (excludes the core_dip buy-and-hold sleeve)."""
        return [p for p in self.positions if p.direction != "core"]

    def equity(self) -> float:
        return self.cash + sum(p.value() for p in self.positions)

    def buying_power(self) -> float:
        return self.cash - sum(p.collateral for p in self.positions)

    def position_for(self, symbol: str) -> Position | None:
        return next((p for p in self.trading_positions() if p.symbol == symbol), None)


class Engine:
    def __init__(self, cfg: Config, portfolio: Portfolio | None = None):
        self.cfg = cfg
        self.pf = portfolio or Portfolio(cash=cfg.starting_cash)
        self.log: list[str] = []

    # ---- pricing helpers -------------------------------------------------
    def _iv(self, bar: Bar) -> float:
        return max(bar.vol * self.cfg.iv_multiplier, self.cfg.iv_floor)

    def model_mark(self, p: Position, today: date, bar: Bar) -> float:
        if p.kind == "stock":
            return bar.close
        T = year_frac(today, p.expiry)
        return bs_price(bar.close, p.strike, T, self.cfg.risk_free_rate, self._iv(bar), p.kind)

    def _slip(self, price: float, kind: str = "call") -> float:
        if kind == "stock":
            return price * self.cfg.stock_slippage_pct
        return max(price * self.cfg.slippage_pct, self.cfg.min_slippage)

    def _fees(self, qty: float, kind: str = "call") -> float:
        return 0.0 if kind == "stock" else self.cfg.fee_per_contract * qty

    # ---- one trading day --------------------------------------------------
    def step(self, today: date, bars: dict[str, Bar], quotes: dict[str, float] | None = None) -> None:
        """Run one day. `quotes` optionally maps position label -> real option mid price."""
        quotes = quotes or {}
        self._execute_pending(today, bars, quotes)
        self._mark(today, bars, quotes)
        if self.cfg.structure == "core_dip":
            self._rebalance_core(today, bars)
        self._settle_expired(today, bars)
        self._decide(today, bars)

    def _mark(self, today: date, bars: dict[str, Bar], quotes: dict[str, float]) -> None:
        for p in self.pf.positions:
            if p.label in quotes:
                p.mark = quotes[p.label]
            elif p.symbol in bars:
                p.mark = self.model_mark(p, today, bars[p.symbol])

    def _settle_expired(self, today: date, bars: dict[str, Bar]) -> None:
        for p in list(self.pf.positions):
            if p.expiry <= today and p.symbol in bars:
                p.mark = intrinsic(bars[p.symbol].close, p.strike, p.kind)
                self._close(p, today, p.mark, "expired", slippage=False)

    def _execute_pending(self, today: date, bars: dict[str, Bar], quotes: dict[str, float]) -> None:
        orders, self.pf.pending = self.pf.pending, []
        for o in orders:
            if o.symbol not in bars:
                self.pf.pending.append(o)  # no data today (holiday/halt): retry tomorrow
                continue
            bar = bars[o.symbol]
            if o.action == "close":
                p = self.pf.position_for(o.symbol)
                if p is None:
                    continue
                mid = quotes.get(p.label, self.model_mark(p, today, bar))
                self._close(p, today, mid, o.reason)
            else:
                self._open(today, o.symbol, o.direction, bar)

    def _decide(self, today: date, bars: dict[str, Bar]) -> None:
        cfg = self.cfg
        # exits
        for p in self.pf.trading_positions():
            bar = bars.get(p.symbol)
            if bar is None:
                continue
            reason = None
            dte = (p.expiry - today).days
            held = (today - p.entry_date).days
            pnl = p.pnl_pct()
            if p.kind == "stock":
                target, stop = cfg.share_profit_target, cfg.share_stop_loss
            else:
                target = cfg.profit_target
                stop = cfg.csp_stop_loss if p.side == "short" else cfg.stop_loss
            if target > 0 and pnl >= target:
                reason = "profit_target"
            elif stop > 0 and pnl <= -stop:
                reason = "stop_loss"
            elif p.direction == "bullish" and bar.rsi > cfg.rsi_exit_long:
                reason = "signal_exit"
            elif p.direction == "bearish" and bar.rsi < cfg.rsi_exit_short:
                reason = "signal_exit"
            elif held >= cfg.max_hold_days:
                reason = "time_stop"
            elif dte <= cfg.min_dte_exit:
                reason = "dte_exit"
            if reason:
                self.pf.pending.append(Order("close", p.symbol, reason=reason))
                self.log.append(f"{today} signal CLOSE {p.label} ({reason}, pnl {pnl:+.0%})")

        # entries
        open_ = self.pf.trading_positions()
        busy = {p.symbol for p in open_} | {o.symbol for o in self.pf.pending}
        slots = cfg.max_positions - len(open_) - sum(
            o.action == "open" for o in self.pf.pending
        )
        for sym in cfg.symbols:
            if slots <= 0:
                break
            bar = bars.get(sym)
            if bar is None or sym in busy:
                continue
            direction = None
            if bar.rsi < cfg.rsi_entry_long and bar.close > bar.sma:
                direction = "bullish"
            elif (
                cfg.allow_bearish
                and cfg.structure == "long_option"
                and bar.rsi > cfg.rsi_entry_short
                and bar.close < bar.sma
            ):
                direction = "bearish"
            if direction:
                self.pf.pending.append(Order("open", sym, direction=direction))
                self.log.append(f"{today} signal OPEN {sym} {direction} (RSI {bar.rsi:.1f})")
                slots -= 1

    # ---- fills -------------------------------------------------------------
    def _open(self, today: date, symbol: str, direction: str, bar: Bar) -> None:
        cfg = self.cfg
        if cfg.structure in ("shares", "core_dip"):
            return self._open_shares(today, symbol, direction, bar)
        expiry = expiry_for_dte(today, cfg.target_dte)
        T = year_frac(today, expiry)
        iv = self._iv(bar)
        r = cfg.risk_free_rate
        if cfg.structure == "cash_secured_put":
            kind, side, delta = "put", "short", cfg.csp_delta
        else:
            kind = "call" if direction == "bullish" else "put"
            side, delta = "long", cfg.long_delta
        strike = strike_for_delta(bar.close, T, r, iv, kind, delta, cfg.strike_increment)
        mid = bs_price(bar.close, strike, T, r, iv, kind)
        if mid < 0.05:
            self.log.append(f"{today} skip {symbol}: premium too small")
            return
        equity = self.pf.equity()
        if side == "long":
            fill = mid + self._slip(mid)
            per_contract = fill * 100 + cfg.fee_per_contract
            n = min(math.floor(equity * cfg.risk_per_trade / per_contract), cfg.max_contracts)
            n = min(n, math.floor(self.pf.buying_power() / per_contract))
        else:
            fill = max(mid - self._slip(mid), 0.01)
            collateral = strike * 100
            n = min(math.floor(equity * cfg.csp_alloc_per_trade / collateral), cfg.max_contracts)
            n = min(n, math.floor(self.pf.buying_power() / collateral))
        if n < 1:
            self.log.append(f"{today} skip {symbol}: position size < 1 contract")
            return
        p = Position(symbol, kind, side, strike, expiry, n, today, fill, direction, mark=mid)
        sign = -1 if side == "long" else 1
        self.pf.cash += sign * fill * 100 * n - self._fees(n)
        self.pf.positions.append(p)
        verb = "BUY" if side == "long" else "SELL"
        self.log.append(f"{today} FILL {verb} {n}x {p.label} @ {fill:.2f}")

    def _rebalance_core(self, today: date, bars: dict[str, Bar]) -> None:
        """Monthly: reset the buy-and-hold sleeve to core_weight of equity, while no dip trade is open."""
        cfg, pf = self.cfg, self.pf
        month = today.strftime("%Y-%m")
        if cfg.core_symbol not in bars or pf.last_rebalance == month or pf.trading_positions():
            return
        price = bars[cfg.core_symbol].close
        core = next((p for p in pf.positions if p.direction == "core"), None)
        held = core.qty if core else 0.0
        target = math.floor(pf.equity() * cfg.core_weight / price * 1e6) / 1e6
        diff = target - held
        pf.last_rebalance = month
        if abs(diff) * price < max(1.0, pf.equity() * 0.01):  # skip tiny rebalances
            return
        if diff > 0:
            fill = price + self._slip(price, "stock")
            diff = min(diff, math.floor(pf.cash / fill * 1e6) / 1e6)
            pf.cash -= diff * fill
            if core:
                core.entry_price = (core.entry_price * held + fill * diff) / (held + diff)
                core.qty = held + diff
            else:
                core = Position(cfg.core_symbol, "stock", "long", 0.0, date.max, diff, today,
                                fill, "core", mark=price)
                pf.positions.append(core)
            self.log.append(f"{today} REBALANCE BUY {diff:g} {core.label} @ {fill:.2f}")
        else:
            fill = price - self._slip(price, "stock")
            pf.cash += -diff * fill
            core.qty = held + diff
            self.log.append(f"{today} REBALANCE SELL {-diff:g} {core.label} @ {fill:.2f}")

    def _open_shares(self, today: date, symbol: str, direction: str, bar: Bar) -> None:
        if direction != "bullish":  # long-only: no shorting
            return
        fill = bar.close + self._slip(bar.close, "stock")
        budget = min(self.pf.equity() * self.cfg.share_alloc_per_trade, self.pf.buying_power())
        qty = math.floor(budget / fill * 1e6) / 1e6  # fractional shares, 6 dp like Robinhood
        if qty * fill < 1.0:
            self.log.append(f"{today} skip {symbol}: not enough buying power")
            return
        p = Position(symbol, "stock", "long", 0.0, date.max, qty, today, fill, direction, mark=bar.close)
        self.pf.cash -= fill * qty
        self.pf.positions.append(p)
        self.log.append(f"{today} FILL BUY {qty:g} {p.label} @ {fill:.2f}")

    def _close(self, p: Position, today: date, mid: float, reason: str, slippage: bool = True) -> None:
        m = p.multiplier
        slip = self._slip(mid, p.kind) if slippage else 0.0
        if p.side == "long":
            fill = max(mid - slip, 0.0)
            self.pf.cash += fill * m * p.qty
            pnl = (fill - p.entry_price) * m * p.qty
        else:
            fill = mid + slip
            self.pf.cash -= fill * m * p.qty
            pnl = (p.entry_price - fill) * m * p.qty
        exit_fee = self._fees(p.qty, p.kind) if slippage else 0.0  # no fee on expiration
        self.pf.cash -= exit_fee
        net = pnl - self._fees(p.qty, p.kind) - exit_fee  # entry fee was paid at open
        self.pf.positions.remove(p)
        self.pf.trades.append(
            Trade(p.symbol, p.label, p.side, p.direction, p.qty, p.entry_date, today,
                  p.entry_price, fill, net, reason)
        )
        verb = "SELL" if p.side == "long" else "BUY"
        self.log.append(f"{today} FILL {verb} {p.qty:g}x {p.label} @ {fill:.2f} ({reason}) pnl {net:+.2f}")


def position_to_dict(p: Position) -> dict:
    d = asdict(p)
    d["expiry"] = p.expiry.isoformat()
    d["entry_date"] = p.entry_date.isoformat()
    return d


def position_from_dict(d: dict) -> Position:
    d = dict(d)
    d["expiry"] = date.fromisoformat(d["expiry"])
    d["entry_date"] = date.fromisoformat(d["entry_date"])
    return Position(**d)
