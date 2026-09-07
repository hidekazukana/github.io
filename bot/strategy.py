"""売買シグナルを出す部分。

戦略を足したいときは Strategy を継承して STRATEGIES に登録するだけでよい。
シグナルは「確定足の列」だけから決まる（純関数）ので、そのままバックテストにも使える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from .market import Candle

BUY = "buy"
SELL = "sell"
HOLD = "hold"


@dataclass
class Signal:
    action: str
    reason: str
    indicators: dict[str, float] = field(default_factory=dict)


def sma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"移動平均に必要な本数が足りません (必要 {period} / 実際 {len(values)})")
    return sum(values[-period:]) / period


class Strategy:
    name = "base"
    warmup = 1

    def signal(self, candles: Sequence[Candle]) -> Signal:  # pragma: no cover - 抽象
        raise NotImplementedError


class SmaCross(Strategy):
    """短期移動平均が長期移動平均を上抜けたら買い、下抜けたら売り。"""

    name = "sma_cross"

    def __init__(self, fast: int = 9, slow: int = 26):
        if fast <= 0 or slow <= 0:
            raise ValueError("fast / slow は正の整数にしてください")
        if fast >= slow:
            raise ValueError("fast は slow より小さくしてください")
        self.fast = fast
        self.slow = slow
        # 1 本前の状態と比べてクロスを判定するので、slow + 1 本必要。
        self.warmup = slow + 1

    def signal(self, candles: Sequence[Candle]) -> Signal:
        if len(candles) < self.warmup:
            return Signal(HOLD, f"足が足りません ({len(candles)}/{self.warmup})")

        closes = [c.close for c in candles]
        fast_now = sma(closes, self.fast)
        slow_now = sma(closes, self.slow)
        fast_prev = sma(closes[:-1], self.fast)
        slow_prev = sma(closes[:-1], self.slow)

        indicators = {
            "fast": round(fast_now, 2),
            "slow": round(slow_now, 2),
            "fast_prev": round(fast_prev, 2),
            "slow_prev": round(slow_prev, 2),
            "close": closes[-1],
        }

        if fast_prev <= slow_prev and fast_now > slow_now:
            return Signal(BUY, f"ゴールデンクロス (SMA{self.fast} > SMA{self.slow})", indicators)
        if fast_prev >= slow_prev and fast_now < slow_now:
            return Signal(SELL, f"デッドクロス (SMA{self.fast} < SMA{self.slow})", indicators)

        trend = "上向き" if fast_now > slow_now else "下向き"
        return Signal(HOLD, f"クロスなし（トレンドは{trend}）", indicators)


class Dca(Strategy):
    """相場を見ずに一定額を積み立てる。買うだけで売らない。"""

    name = "dca"
    warmup = 1

    def signal(self, candles: Sequence[Candle]) -> Signal:
        if not candles:
            return Signal(HOLD, "足がありません")
        return Signal(BUY, "積立（定期買い付け）", {"close": candles[-1].close})


STRATEGIES: dict[str, type[Strategy]] = {
    SmaCross.name: SmaCross,
    Dca.name: Dca,
}


def build_strategy(name: str, params: dict[str, Any] | None = None) -> Strategy:
    if name not in STRATEGIES:
        raise ValueError(f"未知の戦略 '{name}'（使えるのは: {', '.join(sorted(STRATEGIES))}）")
    return STRATEGIES[name](**(params or {}))
