"""
Module: tests/test_regime.py
Purpose: Unit tests for Market Regime Index (MRI) calculation and adaptive capital allocation.
"""
from datetime import date, timedelta
import pytest
from src.core.config import ConfigManager
from src.core.regime import MarketRegimeEngine, MarketRegimeType
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange, InstrumentType, ListingStatus, Security


@pytest.fixture
def regime_setup(tmp_path):
    """Isolated in-memory database and regime engine setup."""
    db = DuckDBManager(":memory:")
    config_mgr = ConfigManager()
    engine = MarketRegimeEngine(db, config_mgr)
    yield engine, db
    db.close()


def test_bull_market_regime_calculation(regime_setup):
    """Verify that a healthy market yields Confirmed Bull regime with 0% cash."""
    engine, db = regime_setup

    # Insert 5 active securities
    for i in range(5):
        db.upsert_securities([
            Security(f"INE00000000{i}", f"BULL_{i}", ListingStatus.NSE_ONLY, InstrumentType.EQUITY, nse_symbol=f"BULL_{i}")
        ])

    # 260 days of quotes in steady uptrends
    quotes = []
    base_date = date(2025, 1, 1)
    for i in range(5):
        for d in range(260):
            p = 100.0 + (d * 1.5)
            dt = base_date + timedelta(days=d)
            quotes.append(EODQuote(
                isin=f"INE00000000{i}", symbol=f"BULL_{i}", exchange=Exchange.NSE, trade_date=dt,
                open_price=p, high_price=p * 1.01, low_price=p * 0.99, close_price=p, prev_close=p - 1.5,
                total_volume=100000, deliverable_volume=60000, delivery_pct=60.0
            ))
    db.upsert_eod_quotes(quotes)

    report = engine.calculate_regime(base_date + timedelta(days=259))
    assert report.regime == MarketRegimeType.CONFIRMED_BULL
    assert report.mri_score >= 75.0
    assert report.pct_above_50_dma == 100.0
    assert report.pct_above_200_dma == 100.0
    assert report.allocation.cash_pct == 0.0
    assert report.allocation.swing_pct + report.allocation.positional_pct + report.allocation.multibagger_pct == 100.0


def test_bear_market_regime_calculation(regime_setup):
    """Verify that a declining market yields Bear regime with 70% cash."""
    engine, db = regime_setup

    for i in range(5):
        db.upsert_securities([
            Security(f"INE99999999{i}", f"BEAR_{i}", ListingStatus.NSE_ONLY, InstrumentType.EQUITY, nse_symbol=f"BEAR_{i}")
        ])

    quotes = []
    base_date = date(2025, 1, 1)
    for i in range(5):
        for d in range(260):
            p = 500.0 - (d * 1.5)
            dt = base_date + timedelta(days=d)
            quotes.append(EODQuote(
                isin=f"INE99999999{i}", symbol=f"BEAR_{i}", exchange=Exchange.NSE, trade_date=dt,
                open_price=p, high_price=p * 1.01, low_price=p * 0.99, close_price=p, prev_close=p + 1.5,
                total_volume=100000, deliverable_volume=60000, delivery_pct=60.0
            ))
    db.upsert_eod_quotes(quotes)

    report = engine.calculate_regime(base_date + timedelta(days=259))
    assert report.regime == MarketRegimeType.CORRECTION_BEAR
    assert report.mri_score < 45.0
    assert report.pct_above_50_dma == 0.0
    assert report.allocation.cash_pct == 70.0
    assert report.allocation.swing_pct == 0.0
