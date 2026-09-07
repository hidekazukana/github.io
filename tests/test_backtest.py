import math

from bot.backtest import load_csv, run
from tests.test_engine import candles, config


def test_backtest_reports_metrics():
    # ジグザグを繰り返してクロスを何度も起こす
    closes = []
    for i in range(30):
        closes += [100, 120, 140, 110, 90, 70]
    result = run(candles(closes), config())
    assert result.trades > 0
    assert result.first < result.last
    assert math.isfinite(result.return_pct)
    assert 0 <= result.max_drawdown <= 100


def test_csv_loader_accepts_iso_and_epoch(tmp_path):
    path = tmp_path / "ohlcv.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-01T00:00:00+00:00,10,12,9,11,1\n"
        "1767225600000,11,13,10,12,2\n",
        encoding="utf-8",
    )
    loaded = load_csv(path)
    assert [c.close for c in loaded] == [11, 12]
    assert loaded[0].timestamp.year == 2026
