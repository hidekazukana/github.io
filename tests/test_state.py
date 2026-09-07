from datetime import datetime, timezone

import pytest

from bot.state import State

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def test_buy_then_sell_realizes_profit():
    s = State(jpy=100_000)
    s.apply_buy(0.01, 5_000_000, fee=50, now=NOW)
    assert s.btc == 0.01
    assert s.jpy == pytest.approx(49_950)
    assert s.avg_entry == pytest.approx(5_005_000)

    pnl = s.apply_sell(0.01, 6_000_000, fee=60, now=NOW)
    assert pnl == pytest.approx(0.01 * 6_000_000 - 60 - 0.01 * 5_005_000)
    assert s.btc == 0
    assert s.avg_entry == 0
    assert s.realized_today(NOW) == pytest.approx(pnl, abs=0.01)


def test_average_entry_blends_two_buys():
    s = State(jpy=100_000)
    s.apply_buy(0.001, 4_000_000, fee=0, now=NOW)
    s.apply_buy(0.001, 6_000_000, fee=0, now=NOW)
    assert s.avg_entry == pytest.approx(5_000_000)


def test_cannot_spend_more_than_balance():
    s = State(jpy=1_000)
    with pytest.raises(ValueError):
        s.apply_buy(0.01, 5_000_000, fee=0, now=NOW)


def test_cannot_sell_more_than_held():
    s = State(jpy=0, btc=0.001, avg_entry=5_000_000)
    with pytest.raises(ValueError):
        s.apply_sell(0.002, 5_000_000, fee=0, now=NOW)


def test_round_trip_through_disk(tmp_path):
    path = tmp_path / "nested" / "state.json"
    s = State(jpy=123.0, btc=0.5, avg_entry=100.0)
    s.save(path)
    loaded = State.load(path, initial_jpy=999)
    assert (loaded.jpy, loaded.btc, loaded.avg_entry) == (123.0, 0.5, 100.0)
    assert loaded.updated_at


def test_missing_file_starts_from_initial_capital(tmp_path):
    assert State.load(tmp_path / "absent.json", initial_jpy=777).jpy == 777
