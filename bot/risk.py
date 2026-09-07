"""発注前の安全弁。

シグナルが出ても、ここを通らない限り注文は作られない。
自動売買で一番大事なのは戦略ではなくこの層なので、独立させてテストしている。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .config import RiskConfig
from .state import State
from .strategy import BUY, HOLD, SELL, Signal

SATOSHI = 8


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    side: str = HOLD
    amount: float = 0.0  # BTC

    @property
    def blocked(self) -> bool:
        return not self.approved


def floor_amount(amount: float) -> float:
    """satoshi 単位に切り捨てる。

    四捨五入だと予算や保有量をわずかに超えることがあり、
    残高不足で注文が弾かれる原因になるので必ず切り捨てる。
    """
    return math.floor(amount * 10**SATOSHI) / 10**SATOSHI


def order_budget(jpy: float, cfg: RiskConfig) -> float:
    """1 回の買いに使える金額。

    残高に対する割合（order_ratio）で決め、order_jpy があればその額で頭を押さえる。
    割合で持つのは、残高が増減しても張り方の比率を保つため
    （order_ratio: 1.0 なら残高がいくつでも常に全額ベットになる）。

    残高いっぱいまで注文すると手数料とスリッページのぶんだけ足りずに取引所へ弾かれるので、
    その余裕を先に差し引いた額を超えないようにする。
    """
    spendable = jpy / (1 + cfg.fee_buffer_rate)
    budget = min(jpy * cfg.order_ratio, spendable)
    if cfg.order_jpy is not None:
        budget = min(budget, cfg.order_jpy)
    return budget


def evaluate(
    signal: Signal,
    state: State,
    price: float,
    cfg: RiskConfig,
    now: datetime,
) -> RiskDecision:
    if price <= 0:
        return RiskDecision(False, "価格が不正です")
    if signal.action == HOLD:
        return RiskDecision(False, "シグナルなし")

    if signal.action == BUY:
        return _evaluate_buy(state, price, cfg, now)
    if signal.action == SELL:
        return _evaluate_sell(state, price, cfg)
    return RiskDecision(False, f"未知のシグナル '{signal.action}'")


def _evaluate_buy(state: State, price: float, cfg: RiskConfig, now: datetime) -> RiskDecision:
    realized = state.realized_today(now)
    if realized <= -abs(cfg.daily_loss_limit_jpy):
        return RiskDecision(
            False,
            f"当日の損失 {realized:,.0f} 円が上限 {cfg.daily_loss_limit_jpy:,.0f} 円に達したため新規買いを停止",
        )

    elapsed = state.minutes_since_last_trade(now)
    if elapsed is not None and elapsed < cfg.cooldown_minutes:
        return RiskDecision(
            False,
            f"クールダウン中（前回の約定から {elapsed:.0f} 分 / {cfg.cooldown_minutes} 分）",
        )

    room_btc = cfg.max_position_btc - state.btc
    if room_btc <= 0:
        return RiskDecision(
            False, f"建玉が上限 {cfg.max_position_btc} BTC に達しています（現在 {state.btc:.8f} BTC）"
        )

    budget = order_budget(state.jpy, cfg)
    amount = floor_amount(min(budget / price, room_btc))
    notional = amount * price

    if notional < cfg.min_order_jpy:
        return RiskDecision(
            False,
            f"注文額 {notional:,.0f} 円が最小注文額 {cfg.min_order_jpy:,.0f} 円を下回ります"
            f"（残高 {state.jpy:,.0f} 円 / 建玉余力 {room_btc:.8f} BTC）",
        )

    if amount < cfg.min_order_btc:
        return RiskDecision(
            False,
            f"注文数量 {amount:.8f} BTC が取引所の最小単位 {cfg.min_order_btc:.8f} BTC を下回ります"
            f"（{notional:,.0f} 円ぶん）",
        )

    return RiskDecision(True, f"{notional:,.0f} 円ぶんの買い", BUY, amount)


def _evaluate_sell(state: State, price: float, cfg: RiskConfig) -> RiskDecision:
    # 手仕舞いはクールダウンと当日損失上限の対象外にしている。
    # 逃げる動きまで止めると、含み損を抱えたまま身動きが取れなくなるため。
    if state.btc <= 0:
        return RiskDecision(False, "建玉がありません")

    amount = state.btc if cfg.sell_all else min(state.btc, cfg.order_jpy / price)
    amount = floor_amount(amount)
    notional = amount * price

    if notional < cfg.min_order_jpy:
        return RiskDecision(
            False,
            f"売却額 {notional:,.0f} 円が最小注文額 {cfg.min_order_jpy:,.0f} 円を下回ります（ダスト）",
        )

    if amount < cfg.min_order_btc:
        # 建玉が最小単位に満たない＝取引所が受け付けないので、手動で処分するしかない
        return RiskDecision(
            False,
            f"建玉 {amount:.8f} BTC が取引所の最小単位 {cfg.min_order_btc:.8f} BTC を下回ります（ダスト）",
        )

    return RiskDecision(True, f"{amount:.8f} BTC の売り", SELL, amount)
