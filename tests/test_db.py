"""
Module: tests/test_db.py
Purpose: Tests for domain models, DuckDB storage schemas, and upserts.
"""
from datetime import date
import pytest
from src.data.models import Exchange, ListingStatus, Security, EODQuote
from src.data.db import DuckDBManager


@pytest.fixture
def db():
    """Isolated in-memory database fixture."""
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_models_validation():
    """Verify domain model constraints."""
    sec = Security(
        isin="INE009A01021", company_name="INFOSYS LTD",
        listing_status=ListingStatus.DUAL_LISTED, nse_symbol="INFY", bse_code="500209"
    )
    assert sec.isin == "INE009A01021"
    with pytest.raises(ValueError):
        Security(isin="SHORT", company_name="Test", listing_status=ListingStatus.NSE_ONLY)


def test_duckdb_securities_upsert_and_get(db):
    """Verify security upsert and query by ISIN, NSE symbol, and BSE code."""
    sec = Security(
        isin="INE009A01021", company_name="INFOSYS LTD",
        listing_status=ListingStatus.DUAL_LISTED, nse_symbol="INFY", bse_code="500209", industry="IT"
    )
    assert db.upsert_securities([sec]) == 1
    assert db.get_security("INE009A01021").nse_symbol == "INFY"
    assert db.get_security("500209").company_name == "INFOSYS LTD"


def test_duckdb_eod_quotes_upsert(db):
    """Verify EOD quote upsert and price/delivery query."""
    quote = EODQuote(
        isin="INE009A01021", symbol="INFY", exchange=Exchange.NSE,
        trade_date=date(2026, 9, 1), open_price=1900.0, high_price=1950.0,
        low_price=1890.0, close_price=1945.0, prev_close=1895.0,
        total_volume=1000000, deliverable_volume=650000, delivery_pct=65.0
    )
    assert db.upsert_eod_quotes([quote]) == 1
    row = db.conn.execute("""
        SELECT symbol, close_price, deliverable_volume, delivery_pct
        FROM eod_quotes WHERE isin = 'INE009A01021' AND exchange = 'NSE' AND trade_date = '2026-09-01';
    """).fetchone()
    assert row == ("INFY", 1945.0, 650000, 65.0)

