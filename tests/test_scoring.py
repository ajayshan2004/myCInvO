"""
Module: tests/test_scoring.py
Purpose: Unit tests for multi-angle rule sub-scores, portfolio lens scores, and confluence.
"""
from datetime import date
import pytest
from src.data.db import DuckDBManager
from src.data.models import QuarterlyFinancial, ShareholdingPattern
from src.radar.scoring import PortfolioScoringEngine


@pytest.fixture
def scoring_setup(tmp_path):
    """Isolated in-memory database and scoring engine setup."""
    db = DuckDBManager(":memory:")
    engine = PortfolioScoringEngine(db, read_only=False)
    yield engine, db
    db.close()


def test_scoring_engine_swing_focus(scoring_setup):
    """Verify swing-specific scoring weighting and portfolio routing."""
    engine, db = scoring_setup
    res = engine.calculate_granular_scores(
        isin="INE111S01011", symbol="SWINGLEAD", company_name="Swing Leader Ltd", trade_date=date(2026, 9, 2),
        trend_score=85.0, vcp_score=80.0, momentum_score=80.0, volume_footprint_score=95.0,
        multibagger_base_score=20.0
    )
    assert res.swing_score >= 80.0
    assert res.primary_portfolio == "SWING"
    assert res.passed_forensic_shield is True


def test_scoring_engine_multibagger_focus(scoring_setup):
    """Verify multibagger-specific scoring weighting with fundamentals."""
    engine, db = scoring_setup
    isin = "INE222M01022"

    db.upsert_quarterly_financials([
        QuarterlyFinancial(isin, "MULTILEAD", date(2025, 9, 30), 200.0, 45.0, 22.5, 30.0, 35.0, 25.0),
        QuarterlyFinancial(isin, "MULTILEAD", date(2025, 12, 31), 260.0, 65.0, 25.0, 45.0, 50.0, 30.0),
    ])
    db.upsert_shareholding_patterns([
        ShareholdingPattern(isin, "MULTILEAD", date(2025, 12, 31), 60.0, 15.0, 10.0, 15.0, pledged_pct=0.0),
    ])

    res = engine.calculate_granular_scores(
        isin=isin, symbol="MULTILEAD", company_name="Multi Compounder", trade_date=date(2026, 9, 2),
        trend_score=85.0, vcp_score=70.0, momentum_score=80.0, volume_footprint_score=75.0,
        multibagger_base_score=95.0
    )
    assert res.multibagger_score >= 80.0
    assert res.earnings_score >= 80.0
    assert res.primary_portfolio == "MULTIBAGGER"


def test_triple_confluence_unicorn(scoring_setup):
    """Verify Triple Confluence Unicorn 🦄 detection across Swing, Positional, and Multibagger."""
    engine, db = scoring_setup
    isin = "INE777U01077"

    db.upsert_quarterly_financials([
        QuarterlyFinancial(isin, "UNICORN", date(2025, 12, 31), 500.0, 150.0, 30.0, 100.0, 65.0, 40.0),
    ])
    db.upsert_shareholding_patterns([
        ShareholdingPattern(isin, "UNICORN", date(2025, 12, 31), 70.0, 15.0, 10.0, 5.0, pledged_pct=0.0),
    ])

    res = engine.calculate_granular_scores(
        isin=isin, symbol="UNICORN", company_name="Unicorn Alpha", trade_date=date(2026, 9, 2),
        trend_score=95.0, vcp_score=90.0, momentum_score=90.0, volume_footprint_score=95.0,
        multibagger_base_score=95.0
    )
    assert res.swing_score >= 75.0
    assert res.positional_score >= 75.0
    assert res.multibagger_score >= 75.0
    assert res.confluence_tag == "Triple Confluence Unicorn 🦄"


def test_dual_confluence_fire(scoring_setup):
    """Verify Dual Confluence 🔥 detection when 2 lenses qualify."""
    engine, db = scoring_setup
    res = engine.calculate_granular_scores(
        isin="INE888D01088", symbol="DUALFIRE", company_name="Dual Setup", trade_date=date(2026, 9, 2),
        trend_score=90.0, vcp_score=85.0, momentum_score=85.0, volume_footprint_score=90.0,
        multibagger_base_score=30.0
    )
    assert res.swing_score >= 75.0
    assert res.positional_score >= 75.0
    assert res.multibagger_score < 75.0
    assert res.confluence_tag == "Dual Confluence 🔥"


def test_forensic_shield_rejection_in_scoring(scoring_setup):
    """Verify forensic failure strips portfolio recommendation and confluence."""
    engine, db = scoring_setup
    res = engine.calculate_granular_scores(
        isin="INE999R01099", symbol="RISKYCORP", company_name="Risky Stock", trade_date=date(2026, 9, 2),
        trend_score=95.0, vcp_score=90.0, momentum_score=90.0, volume_footprint_score=95.0,
        multibagger_base_score=95.0, debt_to_equity=3.5, is_asm_gsm=True
    )
    assert res.passed_forensic_shield is False
    assert res.primary_portfolio == "NONE"
    assert res.confluence_tag is None


def test_empty_fundamental_data_strict_guard(scoring_setup):
    """Verify that when quarterly financials are missing, multibagger score is 0 and unicorn is prevented."""
    engine, db = scoring_setup
    res = engine.calculate_granular_scores(
        isin="INE000EMPTY00", symbol="NODATA", company_name="No Data Corp", trade_date=date(2026, 9, 2),
        trend_score=95.0, vcp_score=90.0, momentum_score=90.0, volume_footprint_score=95.0,
        multibagger_base_score=95.0
    )
    assert res.has_fundamental_data is False
    assert res.multibagger_score == 0.0
    assert res.confluence_tag != "Triple Confluence Unicorn 🦄"
    assert res.primary_portfolio != "MULTIBAGGER"
