"""
Module: tests/test_fundamentals.py
Purpose: Unit tests for Quarterly Financials, Shareholding Patterns, and Forensic Shield.
"""
from datetime import date
import pytest
from src.data.db import DuckDBManager
from src.data.fundamentals import FundamentalsEngine
from src.data.models import QuarterlyFinancial, ShareholdingPattern


@pytest.fixture
def fund_setup(tmp_path):
    """Isolated in-memory database and fundamentals engine setup."""
    db = DuckDBManager(":memory:")
    engine = FundamentalsEngine(db)
    yield engine, db
    db.close()


def test_quarterly_financials_upsert_and_acceleration(fund_setup):
    """Verify quarterly results ingestion and CANSLIM earnings acceleration."""
    engine, db = fund_setup
    isin = "INE123F01012"
    symbol = "GROWTHCORP"

    q1 = QuarterlyFinancial(
        isin=isin, symbol=symbol, period_end=date(2025, 9, 30),
        sales_cr=120.0, operating_profit_cr=24.0, opm_pct=20.0, net_profit_cr=18.0,
        pat_growth_yoy=20.0, sales_growth_yoy=15.0, eps=4.5
    )
    q2 = QuarterlyFinancial(
        isin=isin, symbol=symbol, period_end=date(2025, 12, 31),
        sales_cr=160.0, operating_profit_cr=36.0, opm_pct=22.5, net_profit_cr=28.0,
        pat_growth_yoy=38.5, sales_growth_yoy=28.0, eps=7.0
    )

    count = db.upsert_quarterly_financials([q1, q2])
    assert count == 2

    accel = engine.calculate_earnings_acceleration(isin)
    assert accel["has_data"] is True
    assert accel["is_accelerating"] is True
    assert accel["pat_growth_yoy"] == 38.5
    assert accel["sales_growth_yoy"] == 28.0
    assert accel["opm_expansion_bps"] == 250  # 22.5% - 20.0% = 250 bps
    assert accel["earnings_score"] > 70.0


def test_shareholding_pattern_upsert_and_smart_money(fund_setup):
    """Verify shareholding pattern ingestion and institutional accumulation detection."""
    engine, db = fund_setup
    isin = "INE999S01099"
    symbol = "SMARTACCUM"

    s1 = ShareholdingPattern(
        isin=isin, symbol=symbol, period_end=date(2025, 9, 30),
        promoter_pct=65.0, fii_pct=10.0, dii_pct=8.0, public_retail_pct=17.0,
        pledged_pct=0.0, retail_shareholders_count=45000
    )
    s2 = ShareholdingPattern(
        isin=isin, symbol=symbol, period_end=date(2025, 12, 31),
        promoter_pct=65.0, fii_pct=12.5, dii_pct=9.0, public_retail_pct=13.5,
        pledged_pct=0.0, retail_shareholders_count=41000
    )

    count = db.upsert_shareholding_patterns([s1, s2])
    assert count == 2

    trend = engine.calculate_smart_money_trend(isin)
    assert trend["has_data"] is True
    assert trend["is_accumulating"] is True
    assert trend["inst_holding"] == 21.5  # 12.5 + 9.0
    assert trend["inst_delta_qoq"] == 3.5  # 21.5 - 18.0
    assert trend["pledged_pct"] == 0.0
    assert trend["smart_money_score"] >= 80.0


def test_forensic_shield_clean_and_rejections(fund_setup):
    """Verify Forensic Shield approval for clean stocks and rejection for high pledge or ASM/GSM."""
    engine, db = fund_setup
    isin_clean = "INE001C01001"
    isin_risky = "INE002R01002"

    db.upsert_shareholding_patterns([
        ShareholdingPattern(isin_clean, "CLEAN", date(2025, 12, 31), 60.0, 15.0, 10.0, 15.0, pledged_pct=0.0),
        ShareholdingPattern(isin_risky, "RISKY", date(2025, 12, 31), 50.0, 5.0, 5.0, 40.0, pledged_pct=28.0),
    ])

    # Clean stock: 0% pledge, low debt, high mcap, no ASM/GSM
    clean_eval = engine.evaluate_forensic_shield(isin_clean, debt_to_equity=0.3, is_asm_gsm=False, market_cap_cr=1200.0)
    assert clean_eval["is_clean"] is True
    assert clean_eval["forensic_score"] == 100.0

    # Risky stock: High promoter pledge (28% > 15% threshold)
    risky_eval = engine.evaluate_forensic_shield(isin_risky, debt_to_equity=0.5, is_asm_gsm=False, market_cap_cr=500.0)
    assert risky_eval["is_clean"] is False
    assert risky_eval["pass_pledge"] is False

    # ASM/GSM surveillance rejection
    surveillance_eval = engine.evaluate_forensic_shield(isin_clean, debt_to_equity=0.3, is_asm_gsm=True, market_cap_cr=1200.0)
    assert surveillance_eval["is_clean"] is False
    assert surveillance_eval["pass_surveillance"] is False


def test_fetch_metrics_and_surveillance_sync(fund_setup):
    """Verify company profile metric fetching and surveillance sync."""
    engine, db = fund_setup
    from unittest.mock import MagicMock

    # Mock HTTP client for BSE ComHeader
    mock_http = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "SecurityCode": "500325", "PE": "18.5", "EPS": "42.0",
        "IndustryNew": "Refineries", "Sector": "Energy"
    }
    mock_http.session.get.return_value = mock_resp
    mock_http.timeout = 5

    res = engine.fetch_and_ingest_metrics("INE002A01018", "500325", http_client=mock_http)
    assert res is not None
    assert res["pe_ratio"] == 18.5
    assert res["eps"] == 42.0
    assert res["sector"] == "Energy"

    # Mock surveillance list HTML page
    mock_sur_resp = MagicMock()
    mock_sur_resp.status_code = 200
    mock_sur_resp.text = "<table><tr><td>500999</td><td>INE999A01099</td><td>Shortlisted under GSM Stage 1</td></tr></table>"
    mock_http.session.get.return_value = mock_sur_resp

    sur_set = engine.sync_surveillance_list(http_client=mock_http)
    assert "INE999A01099" in sur_set
    assert "500999" in sur_set
    assert engine.is_surveilled("INE999A01099") is True
    assert engine.is_surveilled("INE000000000") is False


def test_populate_universe_fundamentals(fund_setup):
    """Verify batch population of quarterly financials and shareholding patterns."""
    engine, db = fund_setup
    from src.data.models import Security, ListingStatus
    db.upsert_securities([
        Security(isin="INE001A01001", company_name="Corp A", listing_status=ListingStatus.NSE_ONLY, nse_symbol="CORPA"),
        Security(isin="INE002B01002", company_name="Corp B", listing_status=ListingStatus.BSE_ONLY, bse_code="500002"),
    ])

    count = engine.populate_universe_fundamentals(num_quarters=8)
    assert count == 16  # 2 securities * 8 quarters

    # Verify DuckDB table records
    fin_rows = db.conn.execute("SELECT count(*) FROM quarterly_financials;").fetchone()[0]
    shp_rows = db.conn.execute("SELECT count(*) FROM shareholding_patterns;").fetchone()[0]
    assert fin_rows == 16
    assert shp_rows == 16

    accel = engine.calculate_earnings_acceleration("INE001A01001")
    assert accel["has_data"] is True
    assert accel["pat_growth_yoy"] > 0

    shp = engine.calculate_smart_money_trend("INE001A01001")
    assert shp["has_data"] is True
    assert shp["inst_holding"] > 0

