from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    symbols: list[str] = field(default_factory=lambda: ["SPY", "QQQ", "IWM"])
    # signal
    rsi_period: int = 2
    rsi_entry_long: float = 10
    rsi_entry_short: float = 90
    rsi_exit_long: float = 65
    rsi_exit_short: float = 35
    trend_sma: int = 200
    allow_bearish: bool = True
    # options
    structure: str = "long_option"
    target_dte: int = 30
    long_delta: float = 0.50
    csp_delta: float = 0.30
    strike_increment: float = 1.0
    iv_multiplier: float = 1.15
    iv_floor: float = 0.10
    vol_window: int = 20
    risk_free_rate: float = 0.04
    # exits
    profit_target: float = 0.50
    stop_loss: float = 0.50
    csp_stop_loss: float = 2.00
    max_hold_days: int = 10
    min_dte_exit: int = 5
    # risk
    starting_cash: float = 10_000.0
    risk_per_trade: float = 0.02
    csp_alloc_per_trade: float = 0.25
    max_positions: int = 3
    max_contracts: int = 10
    # costs
    slippage_pct: float = 0.03
    min_slippage: float = 0.01
    fee_per_contract: float = 0.03
    # shares structure
    share_alloc_per_trade: float = 0.33
    share_profit_target: float = 0.0   # 0 disables
    share_stop_loss: float = 0.0       # 0 disables
    stock_slippage_pct: float = 0.0005

    def __post_init__(self) -> None:
        if self.structure not in ("long_option", "cash_secured_put", "shares"):
            raise ValueError(f"unknown structure {self.structure!r}")

    def override(self, key: str, value: str) -> None:
        """Set a field from a command-line string, converting to the field's type."""
        current = getattr(self, key)  # AttributeError for unknown keys
        if isinstance(current, list):
            new = [v.strip().upper() for v in value.split(",") if v.strip()]
        elif isinstance(current, bool):
            new = value.lower() in ("1", "true", "yes")
        else:
            new = type(current)(value)
        setattr(self, key, new)
        self.__post_init__()

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        flat: dict = {}
        for section in raw.values():
            if isinstance(section, dict):
                flat.update(section)
        known = {f.name for f in fields(cls)}
        unknown = set(flat) - known
        if unknown:
            raise ValueError(f"unknown config keys: {sorted(unknown)}")
        return cls(**flat)
