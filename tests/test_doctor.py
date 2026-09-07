"""doctor は ccxt を差し替えられるので、通信もインストールも無しで検証できる。"""

import pytest

from bot.config import Config
from bot.doctor import NG, OK, WARN, run_checks


class FakeExchange:
    def __init__(self, has, timeframes):
        self.has = has
        self.timeframes = timeframes


class FakeCcxt:
    """bitbank は足を返す / bitflyer は返さない、という実際の対応状況を模した偽物。"""

    def bitbank(self):
        return FakeExchange({"fetchOHLCV": True, "createOrder": True}, {"1h": "1hour", "4h": "4hour"})

    def bitflyer(self):
        return FakeExchange({"fetchOHLCV": None, "createOrder": True}, {})


def levels(checks) -> dict[str, str]:
    return {c.name: c.level for c in checks}


def config(**exchange) -> Config:
    cfg = Config()
    for k, v in exchange.items():
        setattr(cfg.exchange, k, v)
    cfg.state.path = "does/not/exist.json"
    return cfg


def test_healthy_setup_passes():
    checks = run_checks(config(id="bitbank"), FakeCcxt(), env={})
    assert NG not in levels(checks).values()


def test_exchange_without_ohlcv_is_flagged():
    checks = run_checks(config(id="bitflyer"), FakeCcxt(), env={})
    assert levels(checks)["足の取得元"] == NG
    assert levels(checks)["発注先"] == OK  # 発注自体はできる


def test_separate_data_source_fixes_it():
    checks = run_checks(config(id="bitflyer", ohlcv_exchange_id="bitbank"), FakeCcxt(), env={})
    assert NG not in levels(checks).values()


def test_unknown_exchange_is_flagged():
    checks = run_checks(config(id="mtgox"), FakeCcxt(), env={})
    assert levels(checks)["足の取得元"] == NG
    assert levels(checks)["発注先"] == NG


def test_unsupported_timeframe_is_flagged():
    checks = run_checks(config(id="bitbank", timeframe="7m"), FakeCcxt(), env={})
    assert levels(checks)["足の長さ"] == NG


def test_live_mode_warns_and_requires_keys():
    cfg = config(id="bitbank")
    cfg.mode.live_enabled = True
    cfg.mode.dry_run = False

    checks = levels(run_checks(cfg, FakeCcxt(), env={}))
    assert checks["実行モード"] == WARN
    assert checks["API キー"] == NG

    checks = levels(
        run_checks(cfg, FakeCcxt(), env={"EXCHANGE_API_KEY": "k", "EXCHANGE_API_SECRET": "s"})
    )
    assert checks["API キー"] == OK


def test_dry_run_does_not_need_keys():
    checks = levels(run_checks(config(id="bitbank"), FakeCcxt(), env={}))
    assert checks["実行モード"] == OK
    assert checks["API キー"] == OK


def test_secrets_are_never_printed():
    cfg = config(id="bitbank")
    checks = run_checks(cfg, FakeCcxt(), env={"EXCHANGE_API_KEY": "SEKRET", "EXCHANGE_API_SECRET": "SEKRET2"})
    assert all("SEKRET" not in c.detail for c in checks)


def test_broken_strategy_params_are_reported():
    cfg = config(id="bitbank")
    cfg.strategy.params = {"fast": 30, "slow": 10}
    assert levels(run_checks(cfg, FakeCcxt(), env={}))["戦略"] == NG


def test_existing_state_file_is_summarised(tmp_path):
    from bot.state import State

    path = tmp_path / "state.json"
    State(jpy=12345.0, btc=0.5, trade_count=3).save(path)
    cfg = config(id="bitbank")
    cfg.state.path = str(path)
    detail = {c.name: c.detail for c in run_checks(cfg, FakeCcxt(), env={})}["状態ファイル"]
    assert "12,345" in detail and "3 回" in detail
