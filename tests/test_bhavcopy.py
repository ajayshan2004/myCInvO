"""
Module: tests/test_bhavcopy.py
Purpose: Tests for bulk Bhavcopy ingestion, delivery % calculation, delta skips, and listeners.
"""
from datetime import date
import pytest
from src.data.models import Exchange
from src.data.db import DuckDBManager
from src.data.bhavcopy import BhavcopyService


@pytest.fixture
def db():
    """Isolated in-memory database fixture."""
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_nse_bhavcopy_parsing_and_delivery_volume(db):
    """Test NSE Bhavcopy parsing with deliverable volume and delivery percentage."""
    service = BhavcopyService(db)
    trade_date = date(2026, 9, 1)

    nse_csv = (
        "SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, PREV_CLOSE, TTL_TRD_QNTY, DELIV_QTY, DELIV_PER, ISIN\n"
        "INFY, EQ, 1900.0, 1950.0, 1890.0, 1945.0, 1895.0, 1000000, 650000, 65.0, INE009A01021\n"
        "TCS, EQ, 4200.0, 4250.0, 4180.0, 4230.0, 4190.0, 500000, 300000, 60.0, INE467B01029\n"
        "DEBT, GS, 100.0, 101.0, 99.0, 100.5, 100.0, 10000, 5000, 50.0, INE999A01099\n"  # Non-equity ignored
    )

    events = []
    service.register_listener(lambda ex, dt, cnt: events.append((ex, dt, cnt)))

    # Initial ingestion
    count = service.ingest_bhavcopy(Exchange.NSE, trade_date, nse_csv)
    assert count == 2
    assert events == [(Exchange.NSE, trade_date, 2)]

    # Verify DuckDB persistence and delivery metrics
    row = db.conn.execute("""
        SELECT symbol, close_price, deliverable_volume, delivery_pct
        FROM eod_quotes WHERE isin = 'INE009A01021' AND exchange = 'NSE' AND trade_date = '2026-09-01';
    """).fetchone()
    assert row == ("INFY", 1945.0, 650000, 65.0)

    # Principle #2: Delta check - re-running for same date should skip immediately
    repeat_count = service.ingest_bhavcopy(Exchange.NSE, trade_date, nse_csv)
    assert repeat_count == 0
    assert len(events) == 1  # No duplicate event


def test_bse_bhavcopy_parsing(db):
    """Test BSE Bhavcopy parsing with ISIN code lookup."""
    service = BhavcopyService(db)
    trade_date = date(2026, 9, 1)

    bse_csv = (
        "SC_CODE, SC_NAME, SC_GROUP, OPEN, HIGH, LOW, CLOSE, PREVCLOSE, NO_OF_SHRS, ISIN_CODE\n"
        "500209, INFY, A, 1905.0, 1952.0, 1892.0, 1947.0, 1898.0, 80000, INE009A01021\n"
    )

    count = service.ingest_bhavcopy(Exchange.BSE, trade_date, bse_csv)
    assert count == 1

    row = db.conn.execute("""
        SELECT symbol, close_price, total_volume
        FROM eod_quotes WHERE isin = 'INE009A01021' AND exchange = 'BSE' AND trade_date = '2026-09-01';
    """).fetchone()
    assert row == ("INFY", 1947.0, 80000)
