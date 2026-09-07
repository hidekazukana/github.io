from datetime import datetime, timedelta, timezone

import pytest

from bot.market import Candle
from bot.strategy import BUY, HOLD, SELL, build_strategy, sma


def candles(closes: list[float]) -> list[Candle]:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(base + timedelta(hours=i), c, c, c, c, 1.0) for i, c in enumerate(closes)
    ]


def test_sma_uses_the_last_n_values():
    assert sma([1, 2, 3, 4], 2) == 3.5


def test_sma_rejects_short_series():
    with pytest.raises(ValueError):
        sma([1, 2], 3)


def test_golden_cross_buys():
    strategy = build_strategy("sma_cross", {"fast": 2, "slow": 4})
    # 下げ続けたあとに急騰させ、短期線を長期線の上に抜けさせる
    signal = strategy.signal(candles([100, 90, 80, 70, 60, 200]))
    assert signal.action == BUY


def test_dead_cross_sells():
    strategy = build_strategy("sma_cross", {"fast": 2, "slow": 4})
    signal = strategy.signal(candles([60, 70, 80, 90, 100, 10]))
    assert signal.action == SELL


def test_no_cross_holds():
    strategy = build_strategy("sma_cross", {"fast": 2, "slow": 4})
    signal = strategy.signal(candles([10, 20, 30, 40, 50, 60]))
    assert signal.action == HOLD


def test_holds_until_warmup_is_satisfied():
    strategy = build_strategy("sma_cross", {"fast": 2, "slow": 4})
    assert strategy.warmup == 5
    assert strategy.signal(candles([1, 2, 3])).action == HOLD


def test_fast_must_be_shorter_than_slow():
    with pytest.raises(ValueError):
        build_strategy("sma_cross", {"fast": 26, "slow": 9})


def test_unknown_strategy_is_rejected():
    with pytest.raises(ValueError):
        build_strategy("moon_phase")


def test_dca_always_buys():
    assert build_strategy("dca").signal(candles([100])).action == BUY
