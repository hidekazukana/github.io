"""過去のローソク足に同じ戦略とリスク設定を当てて、成績を見る。

    python -m bot.backtest --limit 1000
    python -m bot.backtest --csv data/btcjpy_1h.csv

本番と同じ engine.step() を回すので、ここで見えた挙動がそのまま本番の挙動になる
（ただし約定価格は終値ベースの近似で、板の厚みは考慮していない）。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .broker import PaperBroker
from .config import Config
from .engine import step
from .market import Candle, MarketData
from .state import State
from .strategy import build_strategy


@dataclass
class Result:
    initial: float
    final: float
    trades: int
    wins: int
    losses: int
    max_drawdown: float
    buy_hold: float
    first: datetime
    last: datetime

    @property
    def return_pct(self) -> float:
        return (self.final / self.initial - 1) * 100 if self.initial else 0.0

    @property
    def buy_hold_pct(self) -> float:
        return (self.buy_hold / self.initial - 1) * 100 if self.initial else 0.0

    @property
    def win_rate(self) -> float:
        closed = self.wins + self.losses
        return self.wins / closed * 100 if closed else 0.0


def load_csv(path: str | Path) -> list[Candle]:
    """timestamp,open,high,low,close,volume 形式の CSV を読む。

    timestamp は ISO8601 でもミリ秒エポックでもよい。
    """
    candles: list[Candle] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            raw_ts = row["timestamp"]
            if raw_ts.isdigit():
                ts = datetime.fromtimestamp(int(raw_ts) / 1000, tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(raw_ts)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    ts,
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume") or 0),
                )
            )
    return candles


def run(candles: list[Candle], cfg: Config) -> Result:
    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    if len(candles) <= strategy.warmup:
        raise ValueError(f"足が足りません（{len(candles)} 本 / 最低 {strategy.warmup + 1} 本）")

    state = State(jpy=cfg.paper.initial_jpy)
    broker = PaperBroker(cfg.paper.fee_rate, cfg.paper.slippage_rate)

    peak = cfg.paper.initial_jpy
    max_dd = 0.0
    wins = losses = trades = 0

    for i in range(strategy.warmup, len(candles)):
        window = candles[: i + 1]
        result = step(window, state, cfg, strategy, broker, window[-1].timestamp)
        if result.fill:
            trades += 1
            if result.fill.side == "sell":
                if result.fill.pnl > 0:
                    wins += 1
                elif result.fill.pnl < 0:
                    losses += 1
        equity = state.equity(window[-1].close)
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)

    last_close = candles[-1].close
    entry_close = candles[strategy.warmup].close
    return Result(
        initial=cfg.paper.initial_jpy,
        final=state.equity(last_close),
        trades=trades,
        wins=wins,
        losses=losses,
        max_drawdown=max_dd,
        buy_hold=cfg.paper.initial_jpy * (last_close / entry_close),
        first=candles[strategy.warmup].timestamp,
        last=candles[-1].timestamp,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="バックテスト")
    parser.add_argument("--config", default="bot/config.yml")
    parser.add_argument("--limit", type=int, default=500, help="取引所から取る足の本数")
    parser.add_argument("--csv", help="ローソク足 CSV（指定すると取引所には接続しない）")
    args = parser.parse_args(argv)

    cfg = Config.load(args.config)
    cfg.validate()

    if args.csv:
        candles = load_csv(args.csv)
    else:
        market = MarketData(
            cfg.exchange.data_exchange_id, cfg.exchange.symbol, cfg.exchange.timeframe
        )
        candles = market.fetch_closed_candles(limit=args.limit)

    r = run(candles, cfg)
    print(f"期間          : {r.first:%Y-%m-%d %H:%M} 〜 {r.last:%Y-%m-%d %H:%M} UTC")
    print(f"戦略          : {cfg.strategy.name} {cfg.strategy.params}")
    print(f"初期資金      : {r.initial:>14,.0f} 円")
    print(f"最終評価額    : {r.final:>14,.0f} 円 ({r.return_pct:+.2f}%)")
    print(f"単純保有なら  : {r.buy_hold:>14,.0f} 円 ({r.buy_hold_pct:+.2f}%)")
    print(f"取引回数      : {r.trades:>14} 回（勝ち {r.wins} / 負け {r.losses} / 勝率 {r.win_rate:.1f}%）")
    print(f"最大DD        : {r.max_drawdown:>14.2f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
