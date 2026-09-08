"""実弾で注文を出す経路のテスト。

偽の取引所が返す注文の形は、bitbank が実際に返す JSON を ccxt の parse_order に
通した結果に合わせてある。とくに次の 2 点が実物の癖：

  - 成行注文を出した直後は約定が反映されていない
    （executed_amount: "0" / status: UNFILLED → filled=0.0 / status='open'）
  - 注文の応答に手数料は含まれない（fee は常に None）

通信も API キーも使わずに、この癖への対処を確かめる。
"""

from datetime import datetime, timezone

import pytest

from bot.broker import LiveBroker
from bot.config import Config
from bot.risk import RiskDecision
from bot.state import State
from bot.strategy import BUY, SELL

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
SIGNAL_PRICE = 12_000_000.0
FILL_PRICE = 12_131_953.0
AMOUNT = 0.0008


def order(status="open", filled=0.0, average=None, order_id="12345678"):
    """ccxt が bitbank の注文を解釈した後の形。"""
    return {
        "id": order_id,
        "status": status,
        "average": average,
        "price": average,
        "filled": filled,
        "amount": AMOUNT,
        "fee": None,  # bitbank は手数料を返さない
    }


class FakeExchange:
    """注文の状態が呼ぶたびに進んでいく取引所。"""

    def __init__(self, created, fetched=None, cancel_raises=False):
        self.created = created
        self.fetched = list(fetched or [])
        self.cancel_raises = cancel_raises
        self.orders_placed: list[tuple] = []
        self.fetch_calls = 0
        self.cancels: list[str] = []
        self.slept_ms = 0

    def create_order(self, symbol, type_, side, amount, *a, **kw):
        self.orders_placed.append((symbol, type_, side, amount))
        return self.created

    def fetch_order(self, order_id, symbol):
        self.fetch_calls += 1
        if self.fetched:
            return self.fetched.pop(0)
        return self.created

    def cancel_order(self, order_id, symbol):
        self.cancels.append(order_id)
        if self.cancel_raises:
            raise RuntimeError("すでに約定していました")

    def sleep(self, ms):
        self.slept_ms += ms


def broker(exchange) -> LiveBroker:
    cfg = Config()
    cfg.mode.dry_run = False
    cfg.mode.live_enabled = True
    b = LiveBroker(cfg, exchange=exchange)
    b.settle_wait_ms = 0
    return b


def buy(amount=AMOUNT) -> RiskDecision:
    return RiskDecision(True, "買い", BUY, amount)


def sell(amount=AMOUNT) -> RiskDecision:
    return RiskDecision(True, "売り", SELL, amount)


# --- 約定を待つ ------------------------------------------------------


def test_waits_for_the_fill_instead_of_trusting_the_first_response():
    # 出した直後は未約定。次に見たときに埋まっている
    ex = FakeExchange(
        created=order(),
        fetched=[order(status="closed", filled=AMOUNT, average=FILL_PRICE)],
    )
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert ex.fetch_calls == 1
    assert fill is not None
    assert fill.amount == AMOUNT
    assert fill.price == FILL_PRICE


def test_records_the_actual_fill_price_not_the_signal_price():
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert fill.price == FILL_PRICE != SIGNAL_PRICE
    # 帳簿の平均取得単価も実際の約定価格ベースになる
    assert state.avg_entry == pytest.approx(
        (AMOUNT * FILL_PRICE + fill.fee) / AMOUNT
    )


def test_a_filled_order_is_never_cancelled():
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    broker(ex).execute(buy(), SIGNAL_PRICE, State(jpy=10_000), NOW)
    assert ex.cancels == []
    assert ex.fetch_calls == 0


# --- 約定しなかったとき ----------------------------------------------


def test_unfilled_order_is_cancelled_and_nothing_is_recorded():
    # 何度見ても埋まらない
    ex = FakeExchange(created=order(), fetched=[order()] * 10)
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert fill is None  # 取引しなかったのと同じ
    assert ex.cancels == ["12345678"]  # 板に残さない
    assert state.jpy == 10_000
    assert state.btc == 0
    assert state.trade_count == 0


def test_partial_fill_records_only_what_actually_filled():
    half = AMOUNT / 2
    ex = FakeExchange(
        created=order(),
        fetched=[order()] * 5 + [order(status="canceled", filled=half, average=FILL_PRICE)],
    )
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert ex.cancels == ["12345678"]
    assert fill is not None
    assert fill.amount == half
    assert state.btc == half


def test_a_fill_that_lands_during_cancellation_is_still_recorded():
    # 取消を投げた瞬間に約定していた、というきわどい順序
    ex = FakeExchange(
        created=order(),
        fetched=[order()] * 5 + [order(status="closed", filled=AMOUNT, average=FILL_PRICE)],
        cancel_raises=True,
    )
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert fill is not None
    assert state.btc == AMOUNT


# --- 帳簿を壊さないための歯止め --------------------------------------


def test_a_fill_without_a_price_raises_instead_of_guessing():
    # 約定はしたが価格が取れない。推測で単価を書くと損益がずっと狂う
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=None))
    state = State(jpy=10_000)

    with pytest.raises(RuntimeError, match="価格"):
        broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert state.btc == 0
    assert state.jpy == 10_000


def test_a_fill_above_the_balance_is_recorded_rather_than_rejected():
    # 想定より高く約定した。実際に買えている以上、記録しないほうが危ない
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=20_000_000.0))
    state = State(jpy=10_000)
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, state, NOW)

    assert fill is not None
    assert state.btc == AMOUNT
    assert state.jpy < 0  # 帳簿は苦しいが、持ち高は事実と合っている


# --- 手数料と売り ----------------------------------------------------


def test_fee_is_estimated_from_the_taker_rate():
    # bitbank は注文の応答に手数料を含めないので見積もる
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    b = broker(ex)
    fill = b.execute(buy(), SIGNAL_PRICE, State(jpy=10_000), NOW)
    assert fill.fee == pytest.approx(AMOUNT * FILL_PRICE * b.fee_rate)


def test_sell_realizes_pnl_at_the_actual_fill_price():
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    state = State(jpy=0, btc=AMOUNT, avg_entry=10_000_000.0)
    fill = broker(ex).execute(sell(), SIGNAL_PRICE, state, NOW)

    assert fill.pnl == pytest.approx(AMOUNT * FILL_PRICE - fill.fee - AMOUNT * 10_000_000.0)
    assert state.btc == 0
    assert state.realized_today(NOW) == pytest.approx(fill.pnl, abs=0.01)


# --- 注文の出し方 ----------------------------------------------------


def test_places_a_market_order_for_the_approved_amount():
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    b = broker(ex)
    b.execute(buy(0.0005), SIGNAL_PRICE, State(jpy=10_000), NOW)
    assert ex.orders_placed == [("BTC/JPY", "market", "buy", 0.0005)]


def test_order_id_is_kept_for_reconciliation():
    ex = FakeExchange(created=order(status="closed", filled=AMOUNT, average=FILL_PRICE))
    fill = broker(ex).execute(buy(), SIGNAL_PRICE, State(jpy=10_000), NOW)
    assert fill.order_id == "12345678"
    assert fill.dry_run is False
