"""
Module: tests/test_indices.py
Purpose: Unit tests for Index & Sector ingestion, Sector Rotation rankings, and Mansfield RS.
"""
from datetime import date, timedelta
import pytest
from src.data.db import DuckDBManager
from src.data.indices import IndexIngestionEngine
from src.data.models import EODQuote, Exchange, IndexQuote


@pytest.fixture
def index_setup(tmp_path):
    """Isolated in-memory database and index engine setup."""
    db = DuckDBManager(":memory:")
    engine = IndexIngestionEngine(db)
    yield engine, db
    db.close()


def test_parse_nse_index_csv(index_setup):
    """Verify parsing of raw NSE index archive CSV with declarative filtering."""
    engine, _ = index_setup
    sample_csv = (
        "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield\n"
        "Nifty 50,02-09-2026,24500.00,24650.50,24480.20,24600.00,100.00,0.41,250000000,15000.50,22.5,3.8,1.2\n"
        "Nifty IT,02-09-2026,38000.00,38500.00,37900.00,38400.00,400.00,1.05,45000000,4500.00,28.4,6.2,1.8\n"
        "Nifty 1D Rate Index,02-09-2026,-,-,-,2598.01,0.25,0.01,-,-,-,-,-\n"
        "Nifty50 PR 2x Leverage,02-09-2026,11662.35,11718.00,11593.35,11718.00,-140.00,-1.19,-,-,-,-,-\n"
    )
    # Filtered mode (default): Only tracked indices kept
    quotes = engine.parse_nse_index_csv(sample_csv, filter_tracked=True)
    assert len(quotes) == 2
    assert {q.index_name for q in quotes} == {"Nifty 50", "Nifty IT"}
    assert quotes[0].close_price == 24600.00
    assert quotes[0].pe_ratio == 22.5

    # Unfiltered mode: all rows parsed
    all_quotes = engine.parse_nse_index_csv(sample_csv, filter_tracked=False)
    assert len(all_quotes) == 4


def test_duckdb_index_quotes_upsert(index_setup):
    """Verify DuckDB index_quotes insertion and retrieval."""
    engine, db = index_setup
    dt = date(2026, 9, 2)
    q1 = IndexQuote("Nifty 50", dt, 24500.0, 24650.0, 24480.0, 24600.0, 0.41, 22.5, 3.8, 1.2)
    q2 = IndexQuote("Nifty Auto", dt, 21000.0, 21200.0, 20900.0, 21150.0, 0.75, 18.2, 2.9, 1.5)

    count = db.upsert_index_quotes([q1, q2])
    assert count == 2

    row = db.conn.execute("SELECT close_price, pe_ratio FROM index_quotes WHERE index_name = 'Nifty 50';").fetchone()
    assert row[0] == 24600.0
    assert row[1] == 22.5


def test_sector_rankings_and_mansfield_rs(index_setup):
    """Verify sector momentum ranking and Mansfield Relative Strength calculation."""
    engine, db = index_setup
    base_date = date(2025, 1, 1)

    # 300 days of quotes
    index_quotes = []
    stock_quotes = []
    for i in range(300):
        dt = base_date + timedelta(days=i)
        # Nifty 50 growing at 0.1% per day
        p_nifty = 20000.0 + (i * 20.0)
        index_quotes.append(IndexQuote("Nifty 50", dt, p_nifty, p_nifty, p_nifty, p_nifty, 0.1))

        # Nifty IT growing faster at 0.3% per day
        p_it = 30000.0 + (i * 90.0)
        index_quotes.append(IndexQuote("Nifty IT", dt, p_it, p_it, p_it, p_it, 0.3))

        # Outperforming Stock growing at 0.5% per day
        p_stock = 100.0 + (i * 1.5)
        stock_quotes.append(EODQuote(
            isin="INE777R01077", symbol="RSLEADER", exchange=Exchange.NSE, trade_date=dt,
            open_price=p_stock, high_price=p_stock, low_price=p_stock, close_price=p_stock, prev_close=p_stock,
            total_volume=50000, deliverable_volume=25000, delivery_pct=50.0
        ))

    db.upsert_index_quotes(index_quotes)
    db.upsert_eod_quotes(stock_quotes)

    # Calculate Mansfield RS: Should be positive and strong outperformance
    mrs = engine.calculate_mansfield_rs("INE777R01077", "Nifty 50")
    assert mrs > 0.0

    # Sector Rankings: Nifty IT should show positive return
    rankings = engine.get_sector_rankings()
    assert len(rankings) >= 1
    assert rankings[0][0] == "Nifty IT"
    assert rankings[0][1] > 0.0
