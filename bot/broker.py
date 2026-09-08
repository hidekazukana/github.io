"""注文の執行。

PaperBroker … 発注せず、手数料とスリッページを引いた想定で内部残高だけ動かす
LiveBroker  … ccxt 経由で実際に成行注文を出す

どちらも同じ execute() を持つので、上位（engine）は実弾かどうかを意識しない。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from .config import Config
from .market import load_ccxt
from .risk import RiskDecision, floor_amount
from .state import State
from .strategy import BUY, SELL


@dataclass
class Fill:
    side: str
    amount: float
    price: float
    fee: float
    pnl: float
    dry_run: bool
    order_id: str | None = None

    @property
    def notional(self) -> float:
        return self.amount * self.price


class PaperBroker:
    """約定は「次の瞬間に不利な価格で埋まる」前提で見積もる。"""

    dry_run = True

    def __init__(self, fee_rate: float = 0.0012, slippage_rate: float = 0.0005):
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def execute(self, decision: RiskDecision, price: float, state: State, now: datetime) -> Fill:
        if decision.side == BUY:
            fill_price = price * (1 + self.slippage_rate)
            fee = fill_price * decision.amount * self.fee_rate
            # 手数料込みで残高を超えないところまで数量を落とす
            amount = decision.amount
            cost = amount * fill_price + fee
            if cost > state.jpy:
                amount = floor_amount(state.jpy / (fill_price * (1 + self.fee_rate)))
                fee = fill_price * amount * self.fee_rate
            state.apply_buy(amount, fill_price, fee, now)
            return Fill(BUY, amount, fill_price, fee, 0.0, dry_run=True)

        fill_price = price * (1 - self.slippage_rate)
        fee = fill_price * decision.amount * self.fee_rate
        pnl = state.apply_sell(decision.amount, fill_price, fee, now)
        return Fill(SELL, decision.amount, fill_price, fee, pnl, dry_run=True)


class LiveBroker:
    """実弾。API キーは環境変数から読む（設定ファイルには絶対に置かない）。

    成行注文を出した直後の応答には、まだ約定が反映されていない
    （bitbank は executed_amount: "0" / status: UNFILLED を返す）。
    それを鵜呑みにすると「注文しただけ」を「約定した」として帳簿に書き、
    実際の持ち高とずれていく。約定が確定するまで見届けてから記録する。
    """

    dry_run = False

    # 約定を待つ回数と間隔。成行なので普通は 1 回目で確定する。
    settle_attempts = 5
    settle_wait_ms = 2000

    def __init__(self, cfg: Config, exchange=None):
        self.cfg = cfg
        self.symbol = cfg.exchange.symbol
        # bitbank は注文応答に手数料を含めないので、taker 手数料から見積もる。
        # 帳簿に載る手数料は概算であり、取引所の請求額と完全には一致しない。
        self.fee_rate = cfg.paper.fee_rate
        if exchange is not None:
            self.exchange = exchange
            return

        ccxt = load_ccxt()
        api_key = os.environ.get("EXCHANGE_API_KEY", "")
        secret = os.environ.get("EXCHANGE_API_SECRET", "")
        if not api_key or not secret:
            raise RuntimeError("EXCHANGE_API_KEY / EXCHANGE_API_SECRET が設定されていません")
        if not hasattr(ccxt, cfg.exchange.id):
            raise ValueError(f"ccxt に取引所 '{cfg.exchange.id}' がありません")
        self.exchange = getattr(ccxt, cfg.exchange.id)(
            {"apiKey": api_key, "secret": secret, "enableRateLimit": True}
        )

    def execute(
        self, decision: RiskDecision, price: float, state: State, now: datetime
    ) -> Fill | None:
        """注文を出し、約定したぶんだけを帳簿に書く。

        1 枚も約定しなければ何も記録せず None を返す（取引しなかったのと同じ）。
        """
        order = self.exchange.create_order(self.symbol, "market", decision.side, decision.amount)
        order = self._settle(order)

        filled_amount = float(order.get("filled") or 0.0)
        if filled_amount <= 0:
            return None

        filled_price = float(order.get("average") or order.get("price") or 0.0)
        if filled_price <= 0:
            # 約定したのに価格が分からない。誤った単価で帳簿を汚すより止める。
            raise RuntimeError(
                f"約定 {filled_amount} BTC の価格を取引所から取得できませんでした"
                f"（注文 {order.get('id')}）。手動で確認してください"
            )

        fee = filled_price * filled_amount * self.fee_rate

        if decision.side == BUY:
            # 想定より高く約定して残高を超えることがある。実際に買えてしまった
            # 以上、記録を拒むより残高がマイナスに振れるほうがまだ扱える。
            state.apply_buy(filled_amount, filled_price, fee, now, allow_overdraw=True)
            pnl = 0.0
        else:
            pnl = state.apply_sell(filled_amount, filled_price, fee, now)

        return Fill(
            decision.side,
            filled_amount,
            filled_price,
            fee,
            pnl,
            dry_run=False,
            order_id=str(order.get("id") or ""),
        )

    def _settle(self, order: dict) -> dict:
        """約定が確定するまで注文を見にいく。

        待っても埋まらない注文は取り消す。板に残したままにすると、ボットが
        知らないうちに後から約定して、次回以降ずっと帳簿がずれ続ける。
        取消の時点で一部が約定していることもあるので、最後にもう一度確認する。
        """
        order_id = order.get("id")
        for attempt in range(self.settle_attempts):
            if order.get("status") == "closed":
                return order
            if order_id is None:
                return order
            self.exchange.sleep(self.settle_wait_ms)
            order = self.exchange.fetch_order(order_id, self.symbol)

        if order.get("status") == "closed" or order_id is None:
            return order

        try:
            self.exchange.cancel_order(order_id, self.symbol)
        except Exception:
            # 取り消す直前に約定していた場合など。実際の状態は次の fetch で確かめる。
            pass
        return self.exchange.fetch_order(order_id, self.symbol)


def build_broker(cfg: Config, live: bool) -> PaperBroker | LiveBroker:
    """実発注は 3 つの鍵がそろったときだけ開く。

    1. config の mode.live_enabled: true
    2. 実行時の --live
    3. 環境変数 CONFIRM_LIVE_TRADING=yes

    うっかり実弾が飛ぶ事故を防ぐため、1 つでも欠けたらドライランに落とす。
    """
    if not live:
        return PaperBroker(cfg.paper.fee_rate, cfg.paper.slippage_rate)
    if not cfg.mode.live_enabled:
        raise RuntimeError("--live が指定されましたが config の mode.live_enabled が false です")
    if cfg.mode.dry_run:
        raise RuntimeError("--live が指定されましたが config の mode.dry_run が true です")
    if os.environ.get("CONFIRM_LIVE_TRADING") != "yes":
        raise RuntimeError("実発注には環境変数 CONFIRM_LIVE_TRADING=yes が必要です")
    return LiveBroker(cfg)
