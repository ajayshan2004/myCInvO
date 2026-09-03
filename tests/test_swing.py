"""
Module: tests/test_swing.py
Purpose: Unit tests for Swing portfolio setups (High-Tight Flags and Pocket Pivots).
"""
from datetime import date, timedelta
import pytest
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange, InstrumentType, ListingStatus, Security
from src.radar.models import SwingSetupType
from src.radar.swing import SwingScreener


@pytest.fixture
def swing_setup(tmp_path):
    """Isolated in-memory database and swing screener setup."""
    db = DuckDBManager(":memory:")
    config_mgr = ConfigManager()
    screener = SwingScreener(db, config_mgr)
    yield screener, db
    db.close()


def test_high_tight_flag_detection(swing_setup):
    """Verify High-Tight Flag detection on 45% rally and 8% flag pullback."""
    screener, _ = swing_setup

    base_date = date(2025, 1, 1)
    quotes = []

    # 16-day rally from 100 to 145 (+45%)
    for i in range(16):
        p = 100.0 + (i * 3.0)
        quotes.append(EODQuote(
            isin="INE111S01011", symbol="SWINGCORP", exchange=Exchange.NSE, trade_date=base_date + timedelta(days=i),
            open_price=p, high_price=p * 1.01, low_price=p * 0.99, close_price=p, prev_close=p - 3.0,
            total_volume=100000, deliverable_volume=50000, delivery_pct=50.0
        ))

    # 4-day tight flag consolidation: 145 -> 138 (-4.8%)
    flag_prices = [143.0, 140.0, 139.0, 141.0]
    for j, fp in enumerate(flag_prices):
        quotes.append(EODQuote(
            isin="INE111S01011", symbol="SWINGCORP", exchange=Exchange.NSE, trade_date=base_date + timedelta(days=16 + j),
            open_price=fp, high_price=fp * 1.01, low_price=fp * 0.99, close_price=fp, prev_close=fp,
            total_volume=30000, deliverable_volume=15000, delivery_pct=50.0
        ))

    candidate = screener.detect_high_tight_flag("INE111S01011", "SWINGCORP", "SWING CORP", quotes)
    assert candidate is not None
    assert candidate.setup_type == SwingSetupType.HIGH_TIGHT_FLAG
    assert candidate.stop_loss_price < candidate.close_price
    assert candidate.profit_target_price > candidate.close_price
    assert candidate.risk_reward_ratio >= 2.0


def test_pocket_pivot_detection(swing_setup):
    """Verify Pocket Pivot detection when up-day volume exceeds 10-day down-day max."""
    screener, _ = swing_setup
    base_date = date(2025, 1, 1)
    quotes = []

    # 10 prior days with max down volume of 50,000
    for i in range(10):
        is_down = (i % 2 == 0)
        p = 200.0 - (1.0 if is_down else -1.0)
        vol = 50000 if is_down else 40000
        quotes.append(EODQuote(
            isin="INE222P01022", symbol="PIVOTCORP", exchange=Exchange.NSE, trade_date=base_date + timedelta(days=i),
            open_price=p, high_price=p + 1.0, low_price=p - 1.0, close_price=p,
            prev_close=p + 1.0 if is_down else p - 1.0,
            total_volume=vol, deliverable_volume=vol // 2, delivery_pct=50.0
        ))

    # Day 11: Up-day with 120,000 volume (2.4x down-day peak)
    quotes.append(EODQuote(
        isin="INE222P01022", symbol="PIVOTCORP", exchange=Exchange.NSE, trade_date=base_date + timedelta(days=10),
        open_price=201.0, high_price=206.0, low_price=200.5, close_price=205.0, prev_close=200.0,
        total_volume=120000, deliverable_volume=60000, delivery_pct=50.0
    ))

    candidate = screener.detect_pocket_pivot("INE222P01022", "PIVOTCORP", "PIVOT CORP", quotes)
    assert candidate is not None
    assert candidate.setup_type == SwingSetupType.POCKET_PIVOT
    assert candidate.close_price == 205.0
