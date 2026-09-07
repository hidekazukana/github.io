from datetime import datetime, timedelta, timezone

from bot.config import RiskConfig
from bot.risk import evaluate
from bot.state import State
from bot.strategy import BUY, HOLD, SELL, Signal

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
PRICE = 10_000_000.0


def cfg(**over) -> RiskConfig:
    base = dict(
        order_jpy=10_000,
        min_order_jpy=500,
        max_position_btc=0.01,
        daily_loss_limit_jpy=5_000,
        cooldown_minutes=60,
        sell_all=True,
    )
    base.update(over)
    return RiskConfig(**base)


def test_hold_never_trades():
    d = evaluate(Signal(HOLD, "-"), State(jpy=1_000_000), PRICE, cfg(), NOW)
    assert d.blocked


def test_buy_sizes_by_order_jpy():
    d = evaluate(Signal(BUY, "-"), State(jpy=1_000_000), PRICE, cfg(), NOW)
    assert d.approved
    assert d.amount == 0.001  # 10,000 円 / 10,000,000 円


def test_buy_is_capped_by_position_limit():
    state = State(jpy=1_000_000, btc=0.0095, avg_entry=PRICE)
    d = evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW)
    assert d.approved
    assert d.amount == 0.0005  # 上限 0.01 BTC までの残り


def test_buy_blocked_when_position_is_full():
    state = State(jpy=1_000_000, btc=0.01, avg_entry=PRICE)
    assert evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW).blocked


def test_buy_blocked_below_minimum_notional():
    d = evaluate(Signal(BUY, "-"), State(jpy=400), PRICE, cfg(), NOW)
    assert d.blocked
    assert "最小注文額" in d.reason


def test_buy_blocked_during_cooldown():
    state = State(jpy=1_000_000, last_trade_at=(NOW - timedelta(minutes=30)).isoformat())
    d = evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW)
    assert d.blocked
    assert "クールダウン" in d.reason


def test_buy_allowed_after_cooldown():
    state = State(jpy=1_000_000, last_trade_at=(NOW - timedelta(minutes=61)).isoformat())
    assert evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW).approved


def test_buy_blocked_after_daily_loss_limit():
    state = State(jpy=1_000_000, realized_pnl_by_day={"2026-01-10": -5_000})
    d = evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW)
    assert d.blocked
    assert "損失" in d.reason


def test_yesterdays_loss_does_not_block_today():
    state = State(jpy=1_000_000, realized_pnl_by_day={"2026-01-09": -50_000})
    assert evaluate(Signal(BUY, "-"), state, PRICE, cfg(), NOW).approved


def test_sell_exits_the_whole_position():
    state = State(jpy=0, btc=0.004, avg_entry=PRICE)
    d = evaluate(Signal(SELL, "-"), state, PRICE, cfg(), NOW)
    assert d.approved
    assert d.amount == 0.004


def test_sell_is_not_blocked_by_cooldown_or_loss_limit():
    state = State(
        jpy=0,
        btc=0.004,
        avg_entry=PRICE,
        last_trade_at=NOW.isoformat(),
        realized_pnl_by_day={"2026-01-10": -100_000},
    )
    assert evaluate(Signal(SELL, "-"), state, PRICE, cfg(), NOW).approved


def test_sell_blocked_without_position():
    assert evaluate(Signal(SELL, "-"), State(jpy=1_000), PRICE, cfg(), NOW).blocked


def test_sell_blocked_for_dust():
    state = State(jpy=0, btc=0.00001, avg_entry=PRICE)  # 100 円ぶん
    d = evaluate(Signal(SELL, "-"), state, PRICE, cfg(), NOW)
    assert d.blocked
    assert "ダスト" in d.reason


def test_buy_blocked_below_exchange_minimum_amount():
    # 1 BTC = 5 億円まで上がると、10,000 円では 0.00002 BTC しか買えない
    d = evaluate(Signal(BUY, "-"), State(jpy=1_000_000), 500_000_000.0, cfg(), NOW)
    assert d.blocked
    assert "最小単位" in d.reason


def test_buy_allowed_at_exactly_the_minimum_amount():
    # 10,000 円 / 1 億円 = ちょうど 0.0001 BTC
    d = evaluate(Signal(BUY, "-"), State(jpy=1_000_000), 100_000_000.0, cfg(max_position_btc=1), NOW)
    assert d.approved
    assert d.amount == 0.0001


def test_sell_blocked_when_position_is_below_exchange_minimum():
    state = State(jpy=0, btc=0.00005, avg_entry=PRICE)  # 500 円ぶん = 円建て下限は満たす
    d = evaluate(Signal(SELL, "-"), state, PRICE, cfg(min_order_jpy=100), NOW)
    assert d.blocked
    assert "最小単位" in d.reason
