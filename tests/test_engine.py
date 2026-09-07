from datetime import datetime, timedelta, timezone

import pytest

from bot.broker import PaperBroker, build_broker
from bot.config import Config
from bot.engine import append_trade_csv, step
from bot.market import Candle
from bot.state import State
from bot.strategy import BUY, SELL, build_strategy

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candles(closes):
    return [Candle(BASE + timedelta(hours=i), c, c, c, c, 1.0) for i, c in enumerate(closes)]


def config(**strategy_params) -> Config:
    cfg = Config()
    cfg.strategy.name = "sma_cross"
    cfg.strategy.params = {"fast": 2, "slow": 4} | strategy_params
    cfg.risk.cooldown_minutes = 0
    # テストは 100 円台のダミー価格を使うので、実運用向けの下限・上限は外しておく
    cfg.risk.order_jpy = 10_000
    cfg.risk.max_position_btc = 1_000
    cfg.risk.min_order_jpy = 1
    cfg.paper.fee_rate = 0.0
    cfg.paper.slippage_rate = 0.0
    cfg.paper.initial_jpy = 1_000_000
    return cfg


def run(closes, state=None, cfg=None, now=None):
    cfg = cfg or config()
    state = state if state is not None else State(jpy=cfg.paper.initial_jpy)
    series = candles(closes)
    return (
        step(
            series,
            state,
            cfg,
            build_strategy(cfg.strategy.name, cfg.strategy.params),
            PaperBroker(cfg.paper.fee_rate, cfg.paper.slippage_rate),
            now or series[-1].timestamp,
        ),
        state,
    )


def test_buy_signal_moves_money_into_btc():
    result, state = run([100, 90, 80, 70, 60, 200])
    assert result.signal.action == BUY
    assert result.traded
    assert state.btc == pytest.approx(10_000 / 200)
    assert state.jpy == pytest.approx(990_000)


def test_hold_signal_leaves_the_state_untouched():
    result, state = run([10, 20, 30, 40, 50, 60])
    assert not result.traded
    assert state.btc == 0
    assert state.jpy == 1_000_000


def test_sell_signal_closes_the_position():
    cfg = config()
    state = State(jpy=0, btc=1.0, avg_entry=1_000_000)
    result, state = run([60, 70, 80, 90, 100, 10], state=state, cfg=cfg)
    assert result.signal.action == SELL
    assert state.btc == 0
    assert state.jpy == pytest.approx(1.0 * 10)


def test_blocked_signal_records_no_trade():
    cfg = config()
    cfg.risk.max_position_btc = 0.0001
    cfg.risk.min_order_jpy = 100_000
    result, state = run([100, 90, 80, 70, 60, 200], cfg=cfg)
    assert result.signal.action == BUY
    assert not result.traded
    assert result.decision.blocked
    assert state.jpy == 1_000_000


def test_paper_buy_never_overdraws_the_balance():
    cfg = config()
    cfg.paper.fee_rate = 0.01
    state = State(jpy=10_000)
    result, state = run([100, 90, 80, 70, 60, 200], state=state, cfg=cfg)
    assert result.traded
    assert state.jpy >= 0


def test_trade_csv_gets_a_header_once(tmp_path):
    path = tmp_path / "trades.csv"
    result, state = run([100, 90, 80, 70, 60, 200])
    append_trade_csv(path, result, state)
    append_trade_csv(path, result, state)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert lines[0].startswith("timestamp,side")
    assert ",buy," in lines[1]


def test_live_requires_every_switch(monkeypatch):
    cfg = Config()
    monkeypatch.delenv("CONFIRM_LIVE_TRADING", raising=False)

    # --live なし → 常にペーパー
    assert isinstance(build_broker(cfg, live=False), PaperBroker)

    # live_enabled が false のまま --live → 拒否
    with pytest.raises(RuntimeError, match="live_enabled"):
        build_broker(cfg, live=True)

    # dry_run が true のまま --live → 拒否
    cfg.mode.live_enabled = True
    with pytest.raises(RuntimeError, match="dry_run"):
        build_broker(cfg, live=True)

    # 確認用の環境変数がない → 拒否
    cfg.mode.dry_run = False
    with pytest.raises(RuntimeError, match="CONFIRM_LIVE_TRADING"):
        build_broker(cfg, live=True)
