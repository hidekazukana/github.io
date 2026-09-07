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
    """実弾。API キーは環境変数から読む（設定ファイルには絶対に置かない）。"""

    dry_run = False

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.symbol = cfg.exchange.symbol
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

    def execute(self, decision: RiskDecision, price: float, state: State, now: datetime) -> Fill:
        order = self.exchange.create_order(self.symbol, "market", decision.side, decision.amount)
        filled_price = float(order.get("average") or order.get("price") or price)
        filled_amount = float(order.get("filled") or decision.amount)
        fee_info = order.get("fee") or {}
        fee = float(fee_info.get("cost") or filled_price * filled_amount * self.cfg.paper.fee_rate)

        if decision.side == BUY:
            state.apply_buy(filled_amount, filled_price, fee, now)
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
