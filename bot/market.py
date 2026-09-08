"""相場データの取得。

ccxt はここでだけ import する（しかも遅延 import）。
おかげで単体テストや backtest --csv は ccxt なしでも動く。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# 取引所によっては 1 回のリクエストで 1 日ぶんしか返さない（bitbank がそう）。
# since を進めながら何度も叩くので、暴走しないよう上限を決めておく。
MAX_FETCH_REQUESTS = 40
DAY_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class Candle:
    """確定済みのローソク足 1 本。時刻は足の開始時刻（UTC）。"""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, row: list) -> "Candle":
        ts, o, h, low, c, v = row[:6]
        return cls(
            timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
            open=float(o),
            high=float(h),
            low=float(low),
            close=float(c),
            volume=float(v or 0.0),
        )


def load_ccxt():
    try:
        import ccxt  # noqa: PLC0415  (依存を任意にするための遅延 import)
    except ImportError as exc:  # pragma: no cover - 実行環境依存
        raise RuntimeError(
            "ccxt が入っていません。pip install -r bot/requirements.txt を実行してください"
        ) from exc
    return ccxt


class MarketData:
    """公開 API からローソク足を取る。API キーは不要。"""

    def __init__(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        max_stale_bars: int = 3,
        exchange=None,
    ):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.timeframe = timeframe
        self.max_stale_bars = max_stale_bars
        self._exchange = exchange

    @property
    def exchange(self):
        if self._exchange is None:
            ccxt = load_ccxt()
            if not hasattr(ccxt, self.exchange_id):
                raise ValueError(f"ccxt に取引所 '{self.exchange_id}' がありません")
            self._exchange = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
        return self._exchange

    def fetch_closed_candles(self, limit: int = 60) -> list[Candle]:
        """確定足だけを古い順で返す。

        1 回のリクエストでは必要な本数に届かない取引所があるので、
        since を進めながら現在に追いつくまで繰り返して継ぎ合わせる。

        取引所は形成中の足も返してくるので、最後の 1 本は捨てる。
        未確定の終値でシグナルを出すと、同じ足の中で売買が踊る。
        """
        exchange = self.exchange
        if not exchange.has.get("fetchOHLCV"):
            raise RuntimeError(
                f"{self.exchange_id} は OHLCV を返しません。"
                " config の exchange.ohlcv_exchange_id に別の取引所を指定してください"
            )

        duration_ms = exchange.parse_timeframe(self.timeframe) * 1000
        now = exchange.milliseconds()
        cursor = now - duration_ms * (limit + 1)

        by_timestamp: dict[int, list] = {}
        for _ in range(MAX_FETCH_REQUESTS):
            if cursor > now:
                break
            rows = exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, since=cursor, limit=limit)
            newest = None
            for row in rows or []:
                by_timestamp[row[0]] = row
                if newest is None or row[0] > newest:
                    newest = row[0]
            if newest is None or newest < cursor:
                # その期間の足が返ってこない（取引が無い、まだ無い日など）。1 日進めて先へ。
                cursor += DAY_MS
            else:
                cursor = newest + duration_ms

        candles = [Candle.from_ccxt(by_timestamp[ts]) for ts in sorted(by_timestamp)]
        closed = candles[:-1]
        if not closed:
            return []

        self._reject_stale(closed[-1], now, duration_ms)
        return closed[-limit:]

    def _reject_stale(self, newest: Candle, now_ms: int, duration_ms: int) -> None:
        """古い足で売買しないための歯止め。

        取引所の API 仕様によっては、黙って何日も前の足が返ってくることがある。
        そのまま判断すると過去の値段で現在の注文を出すことになるので、
        取れたデータが古いときは判断させずに止める。
        """
        age_ms = now_ms - int(newest.timestamp.timestamp() * 1000)
        allowed_ms = duration_ms * (self.max_stale_bars + 1)
        if age_ms > allowed_ms:
            raise RuntimeError(
                f"最新の確定足が古すぎます（{age_ms / 60000:.0f} 分前 / 許容 {allowed_ms / 60000:.0f} 分）。"
                " 取引所からまとまったデータが取れていない可能性があります"
            )
