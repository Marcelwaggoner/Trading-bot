from trading_bot.config import Config


def test_repo_config_loads():
    cfg = Config.load("config.toml")
    assert cfg.symbols and cfg.structure == "shares"


def test_override_types():
    cfg = Config()
    cfg.override("symbols", "xlf, eem")
    cfg.override("max_positions", "5")
    cfg.override("allow_bearish", "false")
    cfg.override("slippage_pct", "0.05")
    assert cfg.symbols == ["XLF", "EEM"] and cfg.max_positions == 5
    assert cfg.allow_bearish is False and cfg.slippage_pct == 0.05
