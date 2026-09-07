"""config.yml を読み込んで、型のついた設定オブジェクトに変換する。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class ExchangeConfig:
    id: str = "bitbank"
    symbol: str = "BTC/JPY"
    timeframe: str = "1h"
    ohlcv_exchange_id: str | None = None

    @property
    def data_exchange_id(self) -> str:
        return self.ohlcv_exchange_id or self.id


@dataclass
class ModeConfig:
    dry_run: bool = True
    live_enabled: bool = False


@dataclass
class StrategyConfig:
    name: str = "sma_cross"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskConfig:
    order_jpy: float = 10000
    min_order_jpy: float = 500
    min_order_btc: float = 0.0001
    max_position_btc: float = 0.01
    daily_loss_limit_jpy: float = 5000
    cooldown_minutes: int = 60
    sell_all: bool = True
    fee_buffer_rate: float = 0.002


@dataclass
class PaperConfig:
    initial_jpy: float = 1000000
    fee_rate: float = 0.0012
    slippage_rate: float = 0.0005


@dataclass
class StateConfig:
    path: str = "bot/state/paper_state.json"
    trades_csv: str = "bot/state/trades.csv"


@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    mode: ModeConfig = field(default_factory=ModeConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    paper: PaperConfig = field(default_factory=PaperConfig)
    state: StateConfig = field(default_factory=StateConfig)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        def section(key: str, klass):
            values = raw.get(key) or {}
            if not isinstance(values, dict):
                raise ValueError(f"config の {key} はマッピングで書いてください")
            known = {f.name for f in klass.__dataclass_fields__.values()}
            unknown = set(values) - known
            if unknown:
                raise ValueError(f"config の {key} に未知のキーがあります: {sorted(unknown)}")
            return klass(**values)

        return cls(
            exchange=section("exchange", ExchangeConfig),
            mode=section("mode", ModeConfig),
            strategy=section("strategy", StrategyConfig),
            risk=section("risk", RiskConfig),
            paper=section("paper", PaperConfig),
            state=section("state", StateConfig),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return cls.from_dict(raw)

    def validate(self) -> None:
        if self.risk.order_jpy < self.risk.min_order_jpy:
            raise ValueError("risk.order_jpy が risk.min_order_jpy を下回っています")
        if self.risk.min_order_btc < 0:
            raise ValueError("risk.min_order_btc は 0 以上にしてください")
        if self.risk.max_position_btc < self.risk.min_order_btc:
            raise ValueError("risk.max_position_btc が risk.min_order_btc を下回っています")
        if self.risk.max_position_btc <= 0:
            raise ValueError("risk.max_position_btc は正の数にしてください")
        if self.risk.daily_loss_limit_jpy <= 0:
            raise ValueError("risk.daily_loss_limit_jpy は正の数にしてください")
        if self.risk.cooldown_minutes < 0:
            raise ValueError("risk.cooldown_minutes は 0 以上にしてください")
        if not 0 <= self.risk.fee_buffer_rate < 1:
            raise ValueError("risk.fee_buffer_rate は 0 以上 1 未満にしてください")
