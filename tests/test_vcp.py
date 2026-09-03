"""
Module: tests/test_vcp.py
Purpose: Unit tests for Volatility Contraction Pattern (VCP) detection, VDU, and pivot breakout logic.
"""
from datetime import date, timedelta
import pytest
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange
from src.radar.models import ScreenerCandidate
from src.radar.vcp import VCPDetector


@pytest.fixture
def vcp_setup(tmp_path):
    """Isolated in-memory database and detector setup."""
    db = DuckDBManager(":memory:")
    config_mgr = ConfigManager()
    detector = VCPDetector(db, config_mgr)
    yield detector, db
    db.close()


def generate_synthetic_vcp_quotes() -> list[EODQuote]:
    """
    Creates a 60-day price series with 3 progressive contractions:
    - Wave 1: 100 -> 80 (-20%) -> 98
    - Wave 2: 98 -> 88 (-10%) -> 97
    - Wave 3: 97 -> 93 (-4%) -> 98 (Breakout with volume)
    """
    base_date = date(2025, 1, 1)
    quotes = []

    # Sequence of prices modeling 3 contractions
    prices = [
        # Wave 1 (days 0-15): 100 -> 80 -> 98
        100, 95, 90, 85, 80, 82, 86, 90, 93, 96, 98,
        # Wave 2 (days 11-25): 98 -> 88 -> 97
        96, 92, 88, 90, 92, 94, 96, 97,
        # Wave 3 (days 20-35): 97 -> 93 -> 97
        96, 94, 93, 94, 95, 96, 97,
        # Tight Base (days 27-45): 97 to 96
        96.5, 96.8, 96.2, 96.5, 96.7, 96.4, 96.8, 97.0, 97.2,
        # Breakout day (day 37): 99 with volume surge
        99.0
    ]

    for i, p in enumerate(prices):
        dt = base_date + timedelta(days=i)
        vol = 250000 if i == len(prices) - 1 else (40000 if i >= 27 else 100000)
        quotes.append(EODQuote(
            isin="INE999V01099", symbol="VCPCORP", exchange=Exchange.NSE, trade_date=dt,
            open_price=p * 0.99, high_price=p * 1.01, low_price=p * 0.98, close_price=p, prev_close=p,
            total_volume=vol, deliverable_volume=vol // 2, delivery_pct=50.0
        ))
    return quotes


def test_vcp_pattern_detection(vcp_setup):
    """Verify detection of valid progressive contraction waves and breakout."""
    detector, _ = vcp_setup
    quotes = generate_synthetic_vcp_quotes()

    pattern = detector.detect_vcp("INE999V01099", "VCPCORP", "VCP CORP LTD", quotes)
    assert pattern is not None
    assert pattern.symbol == "VCPCORP"
    assert len(pattern.contractions) >= 2
    assert pattern.final_contraction_depth_pct <= 8.0
    assert pattern.breakout_detected is True
    assert pattern.is_valid_vcp is True


def test_invalid_short_history(vcp_setup):
    """Verify rejection when quote history is too short."""
    detector, _ = vcp_setup
    short_quotes = generate_synthetic_vcp_quotes()[:10]
    pattern = detector.detect_vcp("INE999V01099", "VCPCORP", "VCP CORP LTD", short_quotes)
    assert pattern is None
