"""ボットの持ち越し状態（残高・建玉・当日損益）。

GitHub Actions のランナーは毎回まっさらなので、状態は JSON にしてリポジトリに
コミットして持ち回す。ファイルが無ければ初期資金から始める。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

JST_DATE_FMT = "%Y-%m-%d"


@dataclass
class State:
    jpy: float = 0.0
    btc: float = 0.0
    avg_entry: float = 0.0
    realized_pnl_by_day: dict[str, float] = field(default_factory=dict)
    last_trade_at: str | None = None
    last_signal: str | None = None
    updated_at: str | None = None
    trade_count: int = 0

    # --- 入出力 ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, initial_jpy: float) -> "State":
        p = Path(path)
        if not p.exists():
            return cls(jpy=float(initial_jpy))
        raw = json.loads(p.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, p)  # 途中で落ちても壊れた JSON が残らないように

    # --- 参照 -----------------------------------------------------------

    def equity(self, price: float) -> float:
        return self.jpy + self.btc * price

    def unrealized_pnl(self, price: float) -> float:
        return (price - self.avg_entry) * self.btc if self.btc > 0 else 0.0

    def realized_today(self, now: datetime) -> float:
        return self.realized_pnl_by_day.get(now.strftime(JST_DATE_FMT), 0.0)

    def minutes_since_last_trade(self, now: datetime) -> float | None:
        if not self.last_trade_at:
            return None
        last = datetime.fromisoformat(self.last_trade_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (now - last).total_seconds() / 60

    # --- 更新 -----------------------------------------------------------

    def apply_buy(
        self, amount: float, price: float, fee: float, now: datetime, allow_overdraw: bool = False
    ) -> None:
        """買いを記録する。

        allow_overdraw は実弾専用。取引所で本当に約定してしまった以上、
        残高計算が合わないからといって記録を拒むと、実際は持っているのに
        帳簿上は持っていないという最悪の食い違いが残る。事実を優先する。
        """
        cost = amount * price + fee
        if cost > self.jpy + 1e-9 and not allow_overdraw:
            raise ValueError("残高より大きい買い注文は約定できません")
        total_btc = self.btc + amount
        # 平均取得単価は手数料込みで持つ（実現損益を実態に寄せるため）
        self.avg_entry = ((self.avg_entry * self.btc) + amount * price + fee) / total_btc
        self.btc = total_btc
        self.jpy -= cost
        self._mark_trade(now)

    def apply_sell(self, amount: float, price: float, fee: float, now: datetime) -> float:
        if amount > self.btc + 1e-12:
            raise ValueError("保有量より多くは売れません")
        proceeds = amount * price - fee
        pnl = proceeds - self.avg_entry * amount
        self.btc -= amount
        self.jpy += proceeds
        if self.btc <= 1e-12:
            self.btc = 0.0
            self.avg_entry = 0.0
        day = now.strftime(JST_DATE_FMT)
        self.realized_pnl_by_day[day] = round(self.realized_pnl_by_day.get(day, 0.0) + pnl, 4)
        self._mark_trade(now)
        return pnl

    def _mark_trade(self, now: datetime) -> None:
        self.last_trade_at = now.isoformat(timespec="seconds")
        self.trade_count += 1
