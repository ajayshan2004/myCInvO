"""
Module: tests/test_multibagger.py
Purpose: Unit tests for Multibagger portfolio screener and multi-year base breakout logic.
"""
from datetime import date, timedelta
import pytest
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange
from src.radar.multibagger import MultibaggerScreener


@pytest.fixture
def multi_setup(tmp_path):
    """Isolated in-memory database and multibagger screener setup."""
    db = DuckDBManager(":memory:")
    config_mgr = ConfigManager()
    screener = MultibaggerScreener(db, config_mgr)
    yield screener, db
    db.close()


def generate_multi_year_quotes(breakout: bool = True) -> list[EODQuote]:
    """Generates 500 trading days of quotes modeling a 2-year consolidation base."""
    base_date = date(2023, 1, 1)
    quotes = []

    # 490 days consolidating between 100 and 150
    for i in range(490):
        dt = base_date + timedelta(days=i)
        p = 120.0 + (15.0 if i % 40 < 20 else -15.0)
        quotes.append(EODQuote(
            isin="INE888M01088", symbol="MULTICORP", exchange=Exchange.NSE, trade_date=dt,
            open_price=p, high_price=min(150.0, p + 2.0), low_price=max(100.0, p - 2.0),
            close_price=p, prev_close=p, total_volume=50000, deliverable_volume=25000, delivery_pct=50.0
        ))

    # Last 10 days: Breakout to 165 (if breakout=True) or staying at 130
    for j in range(10):
        dt = base_date + timedelta(days=490 + j)
        p = (152.0 + j * 1.5) if breakout else 130.0
        vol = 150000 if breakout else 50000
        quotes.append(EODQuote(
            isin="INE888M01088", symbol="MULTICORP", exchange=Exchange.NSE, trade_date=dt,
            open_price=p - 1.0, high_price=p + 2.0, low_price=p - 1.0, close_price=p,
            prev_close=p - 1.0, total_volume=vol, deliverable_volume=vol // 2, delivery_pct=50.0
        ))
    return quotes


def test_multi_year_breakout_detection(multi_setup):
    """Verify detection of multi-year base breakout and weekly 30-EMA calculation."""
    screener, _ = multi_setup
    quotes = generate_multi_year_quotes(breakout=True)

    cand = screener.detect_multi_year_breakout("INE888M01088", "MULTICORP", "MULTI CORP LTD", quotes)
    assert cand is not None
    assert cand.symbol == "MULTICORP"
    assert cand.base_duration_months >= 16
    assert cand.ath_breakout is True
    assert cand.close_price > cand.weekly_30_ema
    assert cand.trend_strength_score > 70.0


def test_rejection_below_multi_year_high(multi_setup):
    """Verify rejection when stock is still trading inside its multi-year range."""
    screener, _ = multi_setup
    quotes = generate_multi_year_quotes(breakout=False)

    cand = screener.detect_multi_year_breakout("INE888M01088", "MULTICORP", "MULTI CORP LTD", quotes)
    assert cand is None
