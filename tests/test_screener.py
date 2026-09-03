"""
Module: tests/test_screener.py
Purpose: Unit tests for Radar Stage-2 Minervini trend screening and dynamic rules integration.
"""
from datetime import date, timedelta
import pytest
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange, InstrumentType, ListingStatus, Security
from src.radar.screener import RadarScreener


@pytest.fixture
def screener_setup(tmp_path):
    """Isolated in-memory database and test config setup."""
    db = DuckDBManager(":memory:")
    cfg_file = tmp_path / "test_rules.yaml"
    cfg_file.write_text("""
positional_portfolio:
  within_52w_high_pct: 15.0
  min_above_52w_low_pct: 30.0
""")
    config_mgr = ConfigManager(str(cfg_file))
    screener = RadarScreener(db, config_mgr)
    yield screener, db, config_mgr
    screener.close()


def test_stage2_screener_alignment(screener_setup):
    """Verify that only stocks in Minervini Stage 2 uptrend are returned."""
    screener, db, _ = screener_setup

    # Insert 2 securities
    sec_a = Security("INE001A01001", "ALPHA MOTORS", ListingStatus.NSE_ONLY, InstrumentType.EQUITY, nse_symbol="ALPHA")
    sec_b = Security("INE002B01002", "BETA COMMODITIES", ListingStatus.NSE_ONLY, InstrumentType.EQUITY, nse_symbol="BETA")
    db.upsert_securities([sec_a, sec_b])

    # Generate 260 days of quotes
    # ALPHA: Consistent uptrend from 100 to 400
    # BETA: Downtrend from 400 to 100
    quotes = []
    base_date = date(2025, 1, 1)
    for i in range(260):
        dt = base_date + timedelta(days=i)
        # ALPHA uptrend
        p_a = 100.0 + (i * 1.2)
        quotes.append(EODQuote(
            isin="INE001A01001", symbol="ALPHA", exchange=Exchange.NSE, trade_date=dt,
            open_price=p_a, high_price=p_a * 1.02, low_price=p_a * 0.98, close_price=p_a, prev_close=p_a - 1.0,
            total_volume=100000, deliverable_volume=60000, delivery_pct=60.0
        ))
        # BETA downtrend
        p_b = 400.0 - (i * 1.1)
        quotes.append(EODQuote(
            isin="INE002B01002", symbol="BETA", exchange=Exchange.NSE, trade_date=dt,
            open_price=p_b, high_price=p_b * 1.02, low_price=p_b * 0.98, close_price=p_b, prev_close=p_b + 1.0,
            total_volume=50000, deliverable_volume=20000, delivery_pct=40.0
        ))

    db.upsert_eod_quotes(quotes)

    # Run screening for the final date
    latest_dt = base_date + timedelta(days=259)
    candidates = screener.screen_stage2(latest_dt)

    assert len(candidates) == 1
    winner = candidates[0]
    assert winner.symbol == "ALPHA"
    assert winner.close_price > winner.dma_50 > winner.dma_150 > winner.dma_200
    assert winner.dma_200_slope_positive is True
    assert winner.trend_score > 80.0
