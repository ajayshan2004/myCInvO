"""
Module: tests/test_scanner.py
Purpose: Unit and integration tests for RadarScanner pipeline and report rendering.
"""
from datetime import date, timedelta
import pytest
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange, IndexQuote, Security, ListingStatus
from src.radar.scanner import RadarScanner


@pytest.fixture
def scanner_setup(tmp_path):
    """Isolated database and scanner setup with populated test data."""
    db = DuckDBManager(":memory:")
    base_date = date(2025, 1, 1)

    # 1. Master Securities
    db.upsert_securities([
        Security("INE001S01001", "Stage2 Leader", ListingStatus.NSE_ONLY, nse_symbol="LEADER"),
        Security("INE002B01002", "Benchmark Stock", ListingStatus.NSE_ONLY, nse_symbol="BENCH"),
    ])

    # 2. 300 days of quotes
    eod_quotes = []
    index_quotes = []
    for i in range(300):
        dt = base_date + timedelta(days=i)
        # Nifty 50 index
        p_nifty = 20000.0 + (i * 15.0)
        index_quotes.append(IndexQuote("Nifty 50", dt, p_nifty, p_nifty, p_nifty, p_nifty, 0.1, pe_ratio=21.0))
        index_quotes.append(IndexQuote("Nifty IT", dt, 30000.0 + (i * 50.0), 30000.0 + (i * 50.0), 30000.0 + (i * 50.0), 30000.0 + (i * 50.0), 0.2, pe_ratio=25.0))

        # Leader stock: Strong Stage-2 uptrend
        p_stock = 100.0 + (i * 1.2)
        eod_quotes.append(EODQuote(
            isin="INE001S01001", symbol="LEADER", exchange=Exchange.NSE, trade_date=dt,
            open_price=p_stock, high_price=p_stock + 2.0, low_price=p_stock - 1.0, close_price=p_stock,
            prev_close=p_stock - 1.2, total_volume=100000, deliverable_volume=60000, delivery_pct=60.0
        ))
        # Benchmark stock
        p_bench = 50.0 + (i * 0.1)
        eod_quotes.append(EODQuote(
            isin="INE002B01002", symbol="BENCH", exchange=Exchange.NSE, trade_date=dt,
            open_price=p_bench, high_price=p_bench, low_price=p_bench, close_price=p_bench,
            prev_close=p_bench, total_volume=10000, deliverable_volume=5000, delivery_pct=50.0
        ))

    db.upsert_index_quotes(index_quotes)
    db.upsert_eod_quotes(eod_quotes)

    scanner = RadarScanner(db, read_only=False)
    yield scanner, db
    db.close()


def test_radar_scanner_end_to_end(scanner_setup):
    """Verify end-to-end scanner execution across regime, screener, scoring, and confluence."""
    scanner, db = scanner_setup
    results = scanner.scan(top_n=5)

    assert "regime" in results
    assert "sector_rankings" in results
    assert results["total_screened"] >= 1
    assert len(results["positional_candidates"]) >= 1

    top_pos = results["positional_candidates"][0]
    assert top_pos.symbol == "LEADER"
    assert top_pos.positional_score > 60.0
    assert top_pos.passed_forensic_shield is True


def test_radar_scanner_print_report(scanner_setup, capsys):
    """Verify report rendering to standard output without error."""
    scanner, db = scanner_setup
    results = scanner.scan(top_n=5)
    scanner.print_report(results)

    captured = capsys.readouterr().out
    assert "ALPHACRAFT INSTITUTIONAL RADAR" in captured
    assert "LEADER" in captured
    assert "LEADING SECTOR GROUPS" in captured
