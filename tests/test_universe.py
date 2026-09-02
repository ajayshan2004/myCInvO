"""
Module: tests/test_universe.py
Purpose: Tests for dual-exchange universe discovery, ISIN reconciliation, delta skips, and listeners.
"""
import pytest
from src.data.models import ListingStatus
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
        "DEBT1,DEBT SECURITY,GS,12-JAN-2020,10,1,INE777A01077,10\n"
    )

    bse_csv = (
        "Security Code,Security Id,Security Name,Status,Group,Face Value,ISIN No,Industry\n"
        "500209,INFY,INFOSYS LTD.,Active,A,5,INE009A01021,Computers - Software\n"
        "599999,BSEONLY,BSE EXCLUSIVE CO,Active,B,10,INE888B01088,Textiles\n"
    )

    notifications = []
    service.register_listener(lambda count: notifications.append(count))

    # Initial sync
    count = service.sync_universe(nse_csv=nse_csv, bse_csv=bse_csv)
    assert count == 3
    assert notifications == [3]

    # Verify Dual-Listed Stock
    infy = db.get_security("INE009A01021")
    assert infy is not None
    assert infy.listing_status == ListingStatus.DUAL_LISTED
    assert infy.nse_symbol == "INFY"
    assert infy.bse_code == "500209"

    # Principle #2: Delta check - identical payload should return 0 updates
    repeat_count = service.sync_universe(nse_csv=nse_csv, bse_csv=bse_csv)
    assert repeat_count == 0
    assert len(notifications) == 1  # No redundant notification

