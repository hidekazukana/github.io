"""相場データ取得のテスト。

bitbank の「1 リクエスト＝1 日ぶん」という癖を偽の取引所で再現しているので、
通信なしで継ぎ合わせ処理を確かめられる。
"""

from datetime import datetime, timezone

import pytest

from bot.market import DAY_MS, MarketData

HOUR_MS = 60 * 60 * 1000
NOW = 1_800_000_000_000  # 適当な基準時刻（ms）


class FakeExchange:
    """1 回の呼び出しで since の属する 1 日ぶんだけ返す取引所（bitbank と同じ癖）。"""

    def __init__(self, days: int = 10, has_ohlcv: bool = True, now: int = NOW):
        self.has = {"fetchOHLCV": has_ohlcv}
        self.calls: list[int] = []
        self._now = now
        # now を含む日を末尾に、days 日ぶんの 1 時間足を用意する
        self.rows: list[list] = []
        start = (now // DAY_MS - days + 1) * DAY_MS
        ts = start
        while ts <= now:
            self.rows.append([ts, 100.0, 101.0, 99.0, 100.0 + (ts // HOUR_MS % 10), 1.0])
            ts += HOUR_MS

    def milliseconds(self) -> int:
        return self._now

    def parse_timeframe(self, timeframe: str) -> int:
        return {"1h": 3600, "1m": 60}[timeframe]

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):
        self.calls.append(since)
        day = since // DAY_MS
        return [r for r in self.rows if r[0] // DAY_MS == day and r[0] >= since]


def market(exchange, **kw) -> MarketData:
    return MarketData("fake", "BTC/JPY", "1h", exchange=exchange, **kw)


def test_stitches_multiple_days_into_one_series():
    ex = FakeExchange()
    candles = market(ex).fetch_closed_candles(limit=60)

    assert len(candles) == 60
    # 1 日ぶんしか返らない取引所なので、複数回の取得が必要
    assert len(ex.calls) > 1
    # 古い順に並び、重複も欠けもない
    stamps = [c.timestamp for c in candles]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    gaps = {(b - a).total_seconds() for a, b in zip(stamps, stamps[1:])}
    assert gaps == {3600}


def test_reaches_up_to_the_present():
    ex = FakeExchange()
    candles = market(ex).fetch_closed_candles(limit=60)
    newest = candles[-1].timestamp
    # 形成中の 1 本を落とすので、最新の確定足は現在の 1 本前
    assert newest == datetime.fromtimestamp((NOW - HOUR_MS) / 1000, tz=timezone.utc)


def test_drops_the_forming_candle():
    ex = FakeExchange()
    candles = market(ex).fetch_closed_candles(limit=60)
    assert all(c.timestamp.timestamp() * 1000 < NOW for c in candles)


class StaleExchange(FakeExchange):
    """since を無視して古い足だけ返す取引所。

    ccxt の bitbank は since から日付を作って「その 1 日」を返すため、
    呼び方を間違えると何日も前の足が黙って返ってくる。それを再現している。
    """

    def fetch_ohlcv(self, symbol, timeframe="1h", since=None, limit=None):
        self.calls.append(since)
        old_day = (self._now - 8 * DAY_MS) // DAY_MS
        return [r for r in self.rows if r[0] // DAY_MS == old_day]


def test_stale_data_is_rejected_instead_of_traded_on():
    # 8 日前の値段で現在の注文を出すくらいなら、止まったほうがよい
    with pytest.raises(RuntimeError, match="古すぎます"):
        market(StaleExchange()).fetch_closed_candles(limit=60)


def test_empty_result_is_not_reported_as_stale():
    ex = FakeExchange()
    ex.rows = [r for r in ex.rows if r[0] <= NOW - 8 * DAY_MS]
    # 足が 1 本も取れないだけなら、呼び出し側が本数不足として扱う
    assert market(ex).fetch_closed_candles(limit=60) == []


def test_recent_enough_data_passes_the_staleness_check():
    ex = FakeExchange()
    ex.rows = [r for r in ex.rows if r[0] <= NOW - 2 * HOUR_MS]
    candles = market(ex).fetch_closed_candles(limit=60)
    assert candles


def test_exchange_without_ohlcv_is_rejected():
    ex = FakeExchange(has_ohlcv=False)
    with pytest.raises(RuntimeError, match="OHLCV"):
        market(ex).fetch_closed_candles()


def test_empty_exchange_returns_nothing():
    ex = FakeExchange()
    ex.rows = []
    assert market(ex).fetch_closed_candles() == []


def test_request_count_is_bounded():
    ex = FakeExchange(days=400)
    market(ex).fetch_closed_candles(limit=60)
    assert len(ex.calls) <= 40
