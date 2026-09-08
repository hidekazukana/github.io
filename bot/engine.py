"""1 回分の判断（シグナル → リスク審査 → 執行）。

live 実行もバックテストもこの関数を通るので、
「バックテストでは通っていたのに本番は別の挙動だった」が起きにくい。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

from .broker import Fill
from .config import Config
from .market import Candle
from .risk import RiskDecision, evaluate
from .state import State
from .strategy import Signal, Strategy

TRADE_CSV_HEADER = [
    "timestamp",
    "side",
    "amount_btc",
    "price_jpy",
    "notional_jpy",
    "fee_jpy",
    "realized_pnl_jpy",
    "jpy_balance",
    "btc_position",
    "equity_jpy",
    "dry_run",
    "reason",
]


@dataclass
class StepResult:
    timestamp: datetime
    price: float
    signal: Signal
    decision: RiskDecision
    fill: Fill | None
    equity: float

    @property
    def traded(self) -> bool:
        return self.fill is not None

    @property
    def unfilled(self) -> bool:
        """審査を通って注文したのに、1 枚も約定しなかった。"""
        return self.decision.approved and self.fill is None


def step(
    candles: Sequence[Candle],
    state: State,
    cfg: Config,
    strategy: Strategy,
    broker,
    now: datetime,
) -> StepResult:
    if not candles:
        raise ValueError("ローソク足が空です")

    price = candles[-1].close
    signal = strategy.signal(candles)
    state.last_signal = signal.action

    decision = evaluate(signal, state, price, cfg.risk, now)
    fill = broker.execute(decision, price, state, now) if decision.approved else None

    return StepResult(now, price, signal, decision, fill, state.equity(price))


def append_trade_csv(path: str | Path, result: StepResult, state: State) -> None:
    """約定を 1 行追記する。確定申告や損益グラフの元データになる。"""
    if result.fill is None:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    is_new = not p.exists()
    fill = result.fill
    with p.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if is_new:
            writer.writerow(TRADE_CSV_HEADER)
        writer.writerow(
            [
                result.timestamp.isoformat(timespec="seconds"),
                fill.side,
                f"{fill.amount:.8f}",
                f"{fill.price:.2f}",
                f"{fill.notional:.2f}",
                f"{fill.fee:.2f}",
                f"{fill.pnl:.2f}",
                f"{state.jpy:.2f}",
                f"{state.btc:.8f}",
                f"{result.equity:.2f}",
                "yes" if fill.dry_run else "no",
                result.signal.reason,
            ]
        )
