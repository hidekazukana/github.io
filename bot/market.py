"""相場データの取得。

ccxt はここでだけ import する（しかも遅延 import）。
おかげで単体テストや backtest --csv は ccxt なしでも動く。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


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

    def __init__(self, exchange_id: str, symbol: str, timeframe: str):
        self.exchange_id = exchange_id
        self.symbol = symbol
        self.timeframe = timeframe
        self._exchange = None

    @property
    def exchange(self):
        if self._exchange is None:
            ccxt = load_ccxt()
            if not hasattr(ccxt, self.exchange_id):
                raise ValueError(f"ccxt に取引所 '{self.exchange_id}' がありません")
            self._exchange = getattr(ccxt, self.exchange_id)({"enableRateLimit": True})
        return self._exchange

    def fetch_closed_candles(self, limit: int = 200) -> list[Candle]:
        """確定足だけを古い順で返す。

        取引所は形成中の足も返してくるので、最後の 1 本は捨てる。
        未確定の終値でシグナルを出すと、同じ足の中で売買が踊る。
        """
        exchange = self.exchange
        if not exchange.has.get("fetchOHLCV"):
            raise RuntimeError(
                f"{self.exchange_id} は OHLCV を返しません。"
                " config の exchange.ohlcv_exchange_id に別の取引所を指定してください"
            )
        rows = exchange.fetch_ohlcv(self.symbol, timeframe=self.timeframe, limit=limit)
        candles = [Candle.from_ccxt(row) for row in rows]
        return candles[:-1] if candles else []
