"""発注する前に設定を点検する。

    python -m bot.doctor

取引所 ID の打ち間違い、ローソク足を返さない取引所の指定、対応していない足の長さ、
実発注に必要な鍵の不足を、注文を出す前に洗い出す。
ccxt の対応状況（has / timeframes）は静的な情報なので、通信せずに判定できる。
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import Config
from .state import State
from .strategy import build_strategy

OK = "OK"
WARN = "注意"
NG = "NG"


@dataclass
class Check:
    name: str
    level: str
    detail: str

    @property
    def failed(self) -> bool:
        return self.level == NG


def _exchange_caps(ccxt_module, exchange_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """(has, timeframes) を返す。取引所が無ければ None。"""
    if not hasattr(ccxt_module, exchange_id):
        return None
    ex = getattr(ccxt_module, exchange_id)()
    return ex.has or {}, ex.timeframes or {}


def run_checks(cfg: Config, ccxt_module, env: Mapping[str, str] | None = None) -> list[Check]:
    env = env if env is not None else os.environ
    checks: list[Check] = []

    # --- 戦略とリスク設定 ---
    try:
        strategy = build_strategy(cfg.strategy.name, cfg.strategy.params)
        checks.append(
            Check("戦略", OK, f"{strategy.name} / 判断に必要な確定足 {strategy.warmup} 本")
        )
    except Exception as exc:
        checks.append(Check("戦略", NG, str(exc)))

    try:
        cfg.validate()
        checks.append(
            Check(
                "リスク設定",
                OK,
                f"1回 {cfg.risk.order_jpy:,.0f} 円 / 建玉上限 {cfg.risk.max_position_btc} BTC"
                f" / 当日損失上限 {cfg.risk.daily_loss_limit_jpy:,.0f} 円",
            )
        )
    except Exception as exc:
        checks.append(Check("リスク設定", NG, str(exc)))

    # --- ローソク足の取得元 ---
    data_id = cfg.exchange.data_exchange_id
    caps = _exchange_caps(ccxt_module, data_id)
    if caps is None:
        checks.append(Check("足の取得元", NG, f"ccxt に取引所 '{data_id}' がありません"))
    else:
        has, timeframes = caps
        if has.get("fetchOHLCV"):
            checks.append(Check("足の取得元", OK, f"{data_id} はローソク足を返します"))
            if timeframes and cfg.exchange.timeframe not in timeframes:
                checks.append(
                    Check(
                        "足の長さ",
                        NG,
                        f"{data_id} は '{cfg.exchange.timeframe}' に非対応です"
                        f"（使えるのは: {', '.join(list(timeframes)[:10])}）",
                    )
                )
            else:
                checks.append(Check("足の長さ", OK, cfg.exchange.timeframe))
        else:
            checks.append(
                Check(
                    "足の取得元",
                    NG,
                    f"{data_id} はローソク足 API を持ちません。"
                    " config の exchange.ohlcv_exchange_id に bitbank などを指定してください",
                )
            )

    # --- 発注先 ---
    trade_id = cfg.exchange.id
    caps = _exchange_caps(ccxt_module, trade_id)
    if caps is None:
        checks.append(Check("発注先", NG, f"ccxt に取引所 '{trade_id}' がありません"))
    elif caps[0].get("createOrder"):
        checks.append(Check("発注先", OK, f"{trade_id} {cfg.exchange.symbol}"))
    else:
        checks.append(Check("発注先", NG, f"{trade_id} は ccxt から発注できません"))

    # --- 実行モード ---
    live_ready = cfg.mode.live_enabled and not cfg.mode.dry_run
    if live_ready:
        checks.append(Check("実行モード", WARN, "★実発注が有効★（--live と CONFIRM_LIVE_TRADING=yes で発注されます）"))
    else:
        checks.append(Check("実行モード", OK, "ドライラン（注文は出ません）"))

    # --- 資格情報（値は絶対に表示しない）---
    has_keys = bool(env.get("EXCHANGE_API_KEY")) and bool(env.get("EXCHANGE_API_SECRET"))
    if live_ready and not has_keys:
        checks.append(Check("API キー", NG, "実発注が有効ですが EXCHANGE_API_KEY / SECRET が未設定です"))
    else:
        checks.append(Check("API キー", OK, "設定済み" if has_keys else "未設定（ドライランには不要）"))

    # --- 状態ファイル ---
    path = Path(cfg.state.path)
    if not path.exists():
        checks.append(Check("状態ファイル", OK, f"{path} は未作成（初回は {cfg.paper.initial_jpy:,.0f} 円から開始）"))
    else:
        try:
            state = State.load(path, cfg.paper.initial_jpy)
            checks.append(
                Check("状態ファイル", OK, f"残高 {state.jpy:,.0f} 円 / 建玉 {state.btc:.8f} BTC / 約定 {state.trade_count} 回")
            )
        except Exception as exc:
            checks.append(Check("状態ファイル", NG, f"{path} を読めません: {exc}"))

    return checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="設定の事前チェック（注文は出しません）")
    parser.add_argument("--config", default="bot/config.yml")
    args = parser.parse_args(argv)

    try:
        cfg = Config.load(args.config)
    except Exception as exc:
        print(f"[{NG}] 設定: {exc}")
        return 1

    from .market import load_ccxt

    checks = run_checks(cfg, load_ccxt())
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.level:>2}] {c.name:<{width}}  {c.detail}")

    failed = [c for c in checks if c.failed]
    print()
    print(f"{len(checks) - len(failed)}/{len(checks)} 項目 OK" if not failed else f"{len(failed)} 項目に問題があります")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
