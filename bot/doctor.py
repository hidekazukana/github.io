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
from .risk import order_budget
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

    # 注文サイズは残高に対する割合で決まるので、先に残高を掴んでおく
    state_path = Path(cfg.state.path)
    state: State | None = None
    state_error: str | None = None
    try:
        state = State.load(state_path, cfg.paper.initial_jpy)
    except Exception as exc:
        state_error = str(exc)
    balance = state.jpy if state else cfg.paper.initial_jpy

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
        budget = order_budget(balance, cfg.risk)
        share = f"残高の {cfg.risk.order_ratio:.0%}"
        cap = f" / 1回の上限 {cfg.risk.order_jpy:,.0f} 円" if cfg.risk.order_jpy is not None else ""
        checks.append(
            Check(
                "リスク設定",
                WARN if cfg.risk.order_ratio >= 1 else OK,
                f"1回 {share} = 残高 {balance:,.0f} 円なら {budget:,.0f} 円{cap}"
                f" / 建玉上限 {cfg.risk.max_position_btc} BTC"
                f" / 当日損失上限 {cfg.risk.daily_loss_limit_jpy:,.0f} 円"
                + ("（★全額ベット★ 1 回の判断に資金の全額が乗ります）" if cfg.risk.order_ratio >= 1 else ""),
            )
        )
        # 価格が上がるほど 1 回あたりの数量は減る。最小単位を割る価格を先に知らせておく。
        if cfg.risk.min_order_btc > 0 and budget > 0:
            ceiling = budget / cfg.risk.min_order_btc
            checks.append(
                Check(
                    "最小注文数量",
                    OK,
                    f"{cfg.risk.min_order_btc:.8f} BTC"
                    f"（BTC が {ceiling:,.0f} 円を超えると {budget:,.0f} 円では発注できなくなります）",
                )
            )
        # 建玉上限が資金より小さいと、全額ベットのつもりでも途中で頭を打つ
        if budget > 0 and cfg.risk.max_position_btc > 0:
            floor_price = budget / cfg.risk.max_position_btc
            if cfg.risk.order_ratio >= 1:
                checks.append(
                    Check(
                        "建玉上限",
                        OK,
                        f"{cfg.risk.max_position_btc} BTC"
                        f"（BTC が {floor_price:,.0f} 円を下回ると、全額ではなくここで頭打ちになります）",
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
    if state_error is not None:
        checks.append(Check("状態ファイル", NG, f"{state_path} を読めません: {state_error}"))
    elif not state_path.exists():
        checks.append(
            Check("状態ファイル", OK, f"{state_path} は未作成（初回は {cfg.paper.initial_jpy:,.0f} 円から開始）")
        )
    else:
        assert state is not None
        checks.append(
            Check(
                "状態ファイル",
                OK,
                f"残高 {state.jpy:,.0f} 円 / 建玉 {state.btc:.8f} BTC / 約定 {state.trade_count} 回",
            )
        )

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
    warned = [c for c in checks if c.level == WARN]
    print()
    if failed:
        print(f"{len(failed)} 項目に問題があります")
    else:
        note = f"（注意 {len(warned)} 件）" if warned else ""
        print(f"{len(checks) - len(warned)}/{len(checks)} 項目 OK{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
