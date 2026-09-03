"""
Module: tests/test_historical.py
Purpose: Unit tests for concurrent historical range ingestion and delta resumption.
"""
from datetime import date
from unittest.mock import MagicMock
import pytest
from src.data.db import DuckDBManager
from src.data.historical import HistoricalIngestionEngine
from src.data.http_client import NSEBSEHttpClient
from src.data.models import Exchange, Security, ListingStatus, InstrumentType


@pytest.fixture
def mock_historical_setup():
    """Isolated in-memory database and mocked HTTP client fixture."""
    db = DuckDBManager(":memory:")
    mock_http = MagicMock(spec=NSEBSEHttpClient)
    mock_http.fetch_nse_master.return_value = None
    mock_http.fetch_bse_master.return_value = None
    engine = HistoricalIngestionEngine(db, mock_http)
    yield engine, db, mock_http
    engine.close()



def test_get_missing_trading_days(mock_historical_setup):
    """Verify that only weekdays not yet in DuckDB are returned."""
    engine, db, _ = mock_historical_setup

    # Insert a quote for 2024-01-15 (Monday)
    db.conn.execute("""
        INSERT INTO eod_quotes (isin, symbol, exchange, trade_date, open_price, high_price, low_price, close_price, prev_close, total_volume, deliverable_volume, delivery_pct)
        VALUES ('INE009A01021', 'INFY', 'NSE', '2024-01-15', 100, 105, 95, 102, 100, 1000, 500, 50.0);
    """)

    # Check range from Friday 2024-01-12 to Tuesday 2024-01-16
    start_dt = date(2024, 1, 12)  # Friday
    end_dt = date(2024, 1, 16)    # Tuesday
    missing = engine.get_missing_trading_days(start_dt, end_dt)

    # Expected: 2024-01-12 (Fri), 2024-01-16 (Tue). Skipping weekend (13, 14) and existing (15).
    assert missing == [date(2024, 1, 12), date(2024, 1, 16)]


def test_concurrent_range_ingestion_and_deduplication(mock_historical_setup):
    """Verify concurrent multi-threaded ingestion and canonical deduplication."""
    engine, db, mock_http = mock_historical_setup

    start_dt = date(2024, 1, 15)
    end_dt = date(2024, 1, 16)

    # Mock NSE CSV for both dates
    mock_http.fetch_nse_bhavcopy.return_value = (
        "SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, PREV_CLOSE, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER, ISIN\n"
        "INFY, EQ, 1900.0, 1950.0, 1890.0, 1945.0, 1895.0, 1000000, 650000, 65.0, INE009A01021\n"
    )

    # Mock BSE CSV for both dates (contains dual INFY and BSE-only stock)
    mock_http.fetch_bse_bhavcopy.return_value = (
        "SC_CODE, SC_NAME, SC_GROUP, OPEN, HIGH, LOW, CLOSE, PREVCLOSE, NO_OF_SHRS, ISIN_CODE\n"
        "500209, INFY, A, 1905.0, 1952.0, 1892.0, 1947.0, 1898.0, 80000, INE009A01021\n"
        "599999, BSEONLY, B, 100.0, 105.0, 98.0, 102.0, 100.0, 50000, INE888B01088\n"
    )

    result = engine.ingest_range(start_dt, end_dt, max_workers=2)
    assert result["total_days"] == 2
    assert result["total_quotes"] == 4  # (1 NSE + 1 BSE-only) * 2 days = 4 quotes

    # Verify DuckDB has exactly 2 quotes per day with 0 duplicates
    total_db_quotes = db.conn.execute("SELECT count(*) FROM eod_quotes;").fetchone()[0]
    assert total_db_quotes == 4

    dups = db.conn.execute(
        "SELECT isin, count(*) FROM eod_quotes GROUP BY isin, trade_date HAVING count(*) > 1;"
    ).fetchall()
    assert len(dups) == 0

    # Resumption test: Re-running the exact same range skips immediately
    resume_res = engine.ingest_range(start_dt, end_dt, max_workers=2)
    assert resume_res["total_days"] == 0
    assert resume_res["total_quotes"] == 0
