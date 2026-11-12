from trading_bot.config import Config


def test_repo_config_loads():
    cfg = Config.load("config.toml")
    assert cfg.symbols and cfg.structure in ("long_option", "cash_secured_put")
