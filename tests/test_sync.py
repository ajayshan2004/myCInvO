"""
Module: tests/test_sync.py
Purpose: Integration tests for MarketDataSync delta ingestion and error resilience.
"""
from datetime import date
from unittest.mock import MagicMock
import pytest
from src.data.db import DuckDBManager
from src.data.http_client import NSEBSEHttpClient
from src.data.sync import MarketDataSync


@pytest.fixture
def mock_sync_setup():
    """Provides an isolated in-memory DB and mock HTTP client."""
    db = DuckDBManager(":memory:")
    mock_http = MagicMock(spec=NSEBSEHttpClient)
    sync = MarketDataSync(db_manager=db, http_client=mock_http)
    yield sync, db, mock_http
    db.close()


def test_sync_universe_delta_behavior(mock_sync_setup):
    """Test universe sync and verify delta skipping on repeated runs."""
    sync, db, mock_http = mock_sync_setup

    mock_http.fetch_nse_master.return_value = (
        "SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,PAID UP VALUE,MARKET LOT,ISIN NUMBER,FACE VALUE\n"
        "INFY,INFOSYS LIMITED,EQ,08-FEB-1995,5,1,INE009A01021,5\n"
    )
    mock_http.fetch_bse_master.return_value = None

    # First run -> 1 security added
    added = sync.sync_universe()
    assert added == 1
    assert db.get_security("INE009A01021") is not None

    # Second run with same payload -> 0 updates (Principle #2: Delta skip)
    repeated = sync.sync_universe()
    assert repeated == 0


def test_sync_bhavcopy_delta_behavior(mock_sync_setup):
    """Test daily Bhavcopy sync and verify delta skipping for NSE and BSE."""
    sync, db, mock_http = mock_sync_setup
    trade_date = date(2026, 9, 1)

    mock_http.fetch_nse_bhavcopy.return_value = (
        "SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, PREV_CLOSE, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER, ISIN\n"
        "INFY, EQ, 1900.0, 1950.0, 1890.0, 1945.0, 1895.0, 1000000, 650000, 65.0, INE009A01021\n"
    )
    mock_http.fetch_bse_bhavcopy.return_value = (
        "SC_CODE, SC_NAME, SC_GROUP, OPEN, HIGH, LOW, CLOSE, PREVCLOSE, NO_OF_SHRS, ISIN_CODE\n"
        "500209, INFY, A, 1905.0, 1952.0, 1892.0, 1947.0, 1898.0, 80000, INE009A01021\n"
        "599999, BSEONLY, B, 100.0, 105.0, 98.0, 102.0, 100.0, 50000, INE888B01088\n"
    )

    # First run -> 1 NSE quote (INFY) + 1 BSE quote (BSEONLY; INFY is deduplicated)
    res = sync.sync_bhavcopy_for_date(trade_date)
    assert res["NSE"] == 1
    assert res["BSE"] == 1
    assert mock_http.fetch_nse_bhavcopy.call_count == 1
    assert mock_http.fetch_bse_bhavcopy.call_count == 1


    # Second run for same date -> skips HTTP calls and returns 0
    res_repeat = sync.sync_bhavcopy_for_date(trade_date)
    assert res_repeat["NSE"] == 0
    assert res_repeat["BSE"] == 0
    assert mock_http.fetch_nse_bhavcopy.call_count == 1
    assert mock_http.fetch_bse_bhavcopy.call_count == 1

