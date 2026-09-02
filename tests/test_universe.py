"""
Module: tests/test_universe.py
Purpose: Tests for dual-exchange universe discovery, ISIN reconciliation, delta skips, and listeners.
"""
import pytest
from src.data.models import InstrumentType, ListingStatus
from src.data.db import DuckDBManager
from src.data.universe import UniverseService


@pytest.fixture
def db():
    """Isolated in-memory database fixture."""
    manager = DuckDBManager(":memory:")
    yield manager
    manager.close()


def test_universe_discovery_and_reconciliation(db):
    """Test full flow: parse NSE/BSE masters, reconcile ISINs, and sync to DuckDB."""
    service = UniverseService(db)

    nse_csv = (
        "SYMBOL,NAME OF COMPANY,SERIES,DATE OF LISTING,PAID UP VALUE,MARKET LOT,ISIN NUMBER,FACE VALUE\n"
        "INFY,INFOSYS LIMITED,EQ,08-FEB-1995,5,1,INE009A01021,5\n"
        "NSEONLY,NSE EXCLUSIVE CO,EQ,12-JAN-2020,10,1,INE999A01099,10\n"
        "GSEC10,GOVT BENCHMARK BOND,GS,12-JAN-2020,10,1,IN0020200011,100\n"
        "NIFTYBEES,NIPPON ETF NIFTY 50,EQ,08-JAN-2002,1,1,INF204KB14I2,1\n"
        "SMETREND,GROWTH SME LTD,SM,15-JUN-2023,10,1,INE555A01055,10\n"
        "HDFCFUND,HDFC TOP 100 GROWTH,MF,10-OCT-2010,10,1,INF179K01014,10\n"
    )

    bse_csv = (
        "Security Code,Security Id,Security Name,Status,Group,Face Value,ISIN No,Industry\n"
        "500209,INFY,INFOSYS LTD.,Active,A,5,INE009A01021,Computers - Software\n"
        "599999,BSEONLY,BSE EXCLUSIVE CO,Active,B,10,INE888B01088,Textiles\n"
    )

    notifications = []
    service.register_listener(lambda count: notifications.append(count))

    # Initial sync (6 NSE + 1 BSE-only = 7 unique ISINs)
    count = service.sync_universe(nse_csv=nse_csv, bse_csv=bse_csv)
    assert count == 7
    assert notifications == [7]

    # Verify Dual-Listed Stock (EQUITY)
    infy = db.get_security("INE009A01021")
    assert infy is not None
    assert infy.listing_status == ListingStatus.DUAL_LISTED
    assert infy.instrument_type == InstrumentType.EQUITY
    assert infy.nse_symbol == "INFY"
    assert infy.bse_code == "500209"

    # Verify SME Equity Classification (Merged into EQUITY)
    sme = db.get_security("INE555A01055")
    assert sme is not None
    assert sme.instrument_type == InstrumentType.EQUITY

    # Verify ETF Distinction
    etf = db.get_security("INF204KB14I2")
    assert etf is not None
    assert etf.instrument_type == InstrumentType.ETF

    # Verify Mutual Fund Scheme Distinction
    mf = db.get_security("INF179K01014")
    assert mf is not None
    assert mf.instrument_type == InstrumentType.MUTUAL_FUND

    # Verify Govt Bond Classification
    gsec = db.get_security("IN0020200011")
    assert gsec is not None
    assert gsec.instrument_type == InstrumentType.GOVT_BOND

    # Principle #2: Delta check - identical payload should return 0 updates
    repeat_count = service.sync_universe(nse_csv=nse_csv, bse_csv=bse_csv)
    assert repeat_count == 0
    assert len(notifications) == 1  # No redundant notification



