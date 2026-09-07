import pytest

from bot.config import Config


def test_loads_the_shipped_config():
    cfg = Config.load("bot/config.yml")
    cfg.validate()
    assert cfg.mode.dry_run is True
    assert cfg.mode.live_enabled is False
    assert cfg.exchange.symbol == "BTC/JPY"


def test_ohlcv_source_falls_back_to_the_trading_exchange():
    cfg = Config.from_dict({"exchange": {"id": "bitflyer"}})
    assert cfg.exchange.data_exchange_id == "bitflyer"
    cfg = Config.from_dict({"exchange": {"id": "bitflyer", "ohlcv_exchange_id": "bitbank"}})
    assert cfg.exchange.data_exchange_id == "bitbank"


def test_typo_in_a_key_is_an_error():
    with pytest.raises(ValueError, match="未知のキー"):
        Config.from_dict({"risk": {"order_yen": 1000}})


def test_order_below_the_minimum_is_rejected():
    cfg = Config.from_dict({"risk": {"order_jpy": 100, "min_order_jpy": 500}})
    with pytest.raises(ValueError):
        cfg.validate()


def test_position_cap_below_the_exchange_minimum_is_rejected():
    cfg = Config.from_dict({"risk": {"max_position_btc": 0.00001, "min_order_btc": 0.0001}})
    with pytest.raises(ValueError, match="min_order_btc"):
        cfg.validate()


def test_order_ratio_must_be_within_range():
    for bad in (0, 1.5, -0.1):
        with pytest.raises(ValueError, match="order_ratio"):
            Config.from_dict({"risk": {"order_ratio": bad}}).validate()


def test_order_jpy_is_optional():
    cfg = Config.from_dict({"risk": {"order_jpy": None}})
    cfg.validate()
    assert cfg.risk.order_jpy is None


def test_shipped_config_is_a_full_bet():
    cfg = Config.load("bot/config.yml")
    assert cfg.risk.order_ratio == 1.0
    assert cfg.risk.order_jpy is None
