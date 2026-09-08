"""エントリポイント。1 回起動 = 1 回の判断（cron から定期的に叩く前提）。

    python -m bot.main                 # ドライラン
    python -m bot.main --live          # 実発注（別途 config と環境変数が必要）
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from .broker import build_broker
from .config import Config
from .engine import append_trade_csv, step
from .market import MarketData
from .state import State
from .strategy import build_strategy

log = logging.getLogger("bot")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ビットコイン自動売買ボット（既定はドライラン）")
    parser.add_argument("--config", default="bot/config.yml", help="設定ファイル")
    parser.add_argument("--live", action="store_true", help="実発注する（要 live_enabled と確認用環境変数）")
    # 取引所によっては 1 日ぶんずつしか返らず、本数を増やすほどリクエスト回数が増える。
    # 判断に要るのは warmup ぶんだけなので、既定は控えめにしておく。
    parser.add_argument("--limit", type=int, default=60, help="取得するローソク足の本数")
    parser.add_argument("--verbose", action="store_true", help="デバッグログを出す")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        cfg = Config.load(args.config)
        cfg.validate()
    except Exception as exc:
        log.error("設定を読めません: %s", exc)
        return 1

    strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
    state = State.load(cfg.state.path, cfg.paper.initial_jpy)

    try:
        broker = build_broker(cfg, args.live)
    except Exception as exc:
        log.error("執行モードを決められません: %s", exc)
        return 1

    mode = "ドライラン" if broker.dry_run else "★実発注★"
    log.info(
        "%s | %s %s %s | 戦略=%s",
        mode,
        cfg.exchange.id,
        cfg.exchange.symbol,
        cfg.exchange.timeframe,
        strategy.name,
    )

    market = MarketData(cfg.exchange.data_exchange_id, cfg.exchange.symbol, cfg.exchange.timeframe)
    try:
        candles = market.fetch_closed_candles(limit=max(args.limit, strategy.warmup + 1))
    except Exception as exc:
        log.error("相場データを取得できません: %s", exc)
        return 1

    if len(candles) < strategy.warmup:
        log.error("確定足が %d 本しかありません（%d 本必要）", len(candles), strategy.warmup)
        return 1

    now = datetime.now(timezone.utc)
    result = step(candles, state, cfg, strategy, broker, now)

    log.info("価格 %s 円 | シグナル=%s（%s）", f"{result.price:,.0f}", result.signal.action, result.signal.reason)
    if result.fill:
        f = result.fill
        log.info(
            "約定 %s %.8f BTC @ %s 円（手数料 %s 円 / 実現損益 %s 円）%s",
            f.side.upper(),
            f.amount,
            f"{f.price:,.0f}",
            f"{f.fee:,.0f}",
            f"{f.pnl:+,.0f}",
            "" if f.dry_run else f" order_id={f.order_id}",
        )
        append_trade_csv(cfg.state.trades_csv, result, state)
    elif result.unfilled:
        log.warning("注文を出しましたが約定しませんでした（%s）。板に残った注文は取り消し済みです", result.decision.reason)
    else:
        log.info("見送り: %s", result.decision.reason)

    log.info(
        "残高 %s 円 | 建玉 %.8f BTC（平均取得 %s 円）| 評価額 %s 円 | 当日実現損益 %s 円",
        f"{state.jpy:,.0f}",
        state.btc,
        f"{state.avg_entry:,.0f}",
        f"{result.equity:,.0f}",
        f"{state.realized_today(now):+,.0f}",
    )

    state.save(cfg.state.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
