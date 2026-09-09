"""
Module: src/radar/scoring.py
Purpose: Granular multi-angle scoring across individual rules, portfolio lenses, and confluence.
"""
from datetime import date
from typing import Any, Dict, List, Optional
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.fundamentals import FundamentalsEngine
from src.data.indices import IndexIngestionEngine
from src.radar.models import OpportunityScore


class PortfolioScoringEngine:
    """Evaluates granular sub-scores, portfolio lens scores, and confluence detection."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        config_manager: Optional[ConfigManager] = None,
        read_only: bool = True,
    ) -> None:
        self.db = db_manager or DuckDBManager(read_only=read_only)
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.get_config()
        self.fundamentals = FundamentalsEngine(self.db, self.config_manager)
        self.indices = IndexIngestionEngine(self.db, config_manager=self.config_manager)

    def calculate_granular_scores(
        self,
        isin: str,
        symbol: str,
        company_name: str,
        trade_date: date,
        trend_score: float = 0.0,
        vcp_score: float = 0.0,
        momentum_score: float = 0.0,
        volume_footprint_score: float = 0.0,
        multibagger_base_score: float = 0.0,
        debt_to_equity: float = 0.0,
        market_cap_cr: float = 500.0,
        is_asm_gsm: bool = False,
    ) -> OpportunityScore:
        """
        PSEUDOCODE:
        1. Evaluate fundamentals (CANSLIM earnings acceleration, Smart Money, Forensics).
        2. Normalize rule sub-scores (0 - 100).
        3. Calculate weighted lens scores: SwingScore, PositionalScore, MultibaggerScore.
        4. Check Forensic Shield pass/fail guard.
        5. Detect multi-horizon confluence (Triple Confluence Unicorn 🦄 / Dual Confluence 🔥).
        6. Return complete OpportunityScore.
        """
        # Fundamental & Forensic evaluation
        earnings_data = self.fundamentals.calculate_earnings_acceleration(isin)
        has_earnings = earnings_data.get("has_data", False)
        earnings_score = earnings_data.get("earnings_score", 0.0)

        smart_money_data = self.fundamentals.calculate_smart_money_trend(isin)
        has_smart_money = smart_money_data.get("has_data", False)
        smart_money_score = smart_money_data.get("smart_money_score", 0.0)

        has_fundamental_data = has_earnings and has_smart_money

        forensic_data = self.fundamentals.evaluate_forensic_shield(
            isin, debt_to_equity=debt_to_equity, is_asm_gsm=is_asm_gsm, market_cap_cr=market_cap_cr
        )
        forensic_score = forensic_data.get("forensic_score", 0.0)
        is_clean = forensic_data.get("is_clean", True)

        # 1. Swing Score (3-20 days): Volume Footprint + Trend + Momentum + VCP
        swing_raw = (
            (volume_footprint_score * 0.35)
            + (trend_score * 0.25)
            + (momentum_score * 0.20)
            + (vcp_score * 0.20)
        )
        swing_score = round(min(100.0, max(0.0, swing_raw)), 1)

        # 2. Positional Score (1-6 months): Stage-2 Trend + VCP + Momentum + Volume (+ Earnings if available)
        if has_earnings:
            positional_raw = (
                (trend_score * 0.30)
                + (vcp_score * 0.25)
                + (momentum_score * 0.20)
                + (volume_footprint_score * 0.15)
                + (earnings_score * 0.10)
            )
        else:
            positional_raw = (
                (trend_score * 0.35)
                + (vcp_score * 0.25)
                + (momentum_score * 0.20)
                + (volume_footprint_score * 0.20)
            )
        positional_score = round(min(100.0, max(0.0, positional_raw)), 1)

        # 3. Multibagger Score (6m-3y+): STRICT GUARD - require quarterly filings & smart money
        if has_fundamental_data:
            multibagger_raw = (
                (multibagger_base_score * 0.30)
                + (earnings_score * 0.25)
                + (smart_money_score * 0.20)
                + (trend_score * 0.15)
                + (forensic_score * 0.10)
            )
            multibagger_score = round(min(100.0, max(0.0, multibagger_raw)), 1)
        else:
            multibagger_score = 0.0

        # Multi-Horizon Confluence Identification
        high_threshold = 75.0
        qualifying = sum([
            1 if swing_score >= high_threshold else 0,
            1 if positional_score >= high_threshold else 0,
            1 if (multibagger_score >= high_threshold and has_fundamental_data) else 0,
        ])

        confluence_tag: Optional[str] = None
        if qualifying == 3 and has_fundamental_data:
            confluence_tag = "Triple Confluence Unicorn 🦄"
        elif qualifying == 2:
            confluence_tag = "Dual Confluence 🔥"

        # Primary Portfolio Routing
        primary_portfolio = "NONE"
        if is_clean:
            scores = [
                ("SWING", swing_score),
                ("POSITIONAL", positional_score),
                ("MULTIBAGGER", multibagger_score if has_fundamental_data else 0.0),
            ]
            best_portfolio, best_score = max(scores, key=lambda x: x[1])
            if best_score >= 65.0:
                primary_portfolio = best_portfolio
        else:
            confluence_tag = None

        return OpportunityScore(
            isin=isin, symbol=symbol, company_name=company_name, trade_date=trade_date,
            trend_score=round(trend_score, 1), vcp_score=round(vcp_score, 1),
            momentum_score=round(momentum_score, 1), volume_footprint_score=round(volume_footprint_score, 1),
            earnings_score=round(earnings_score, 1), smart_money_score=round(smart_money_score, 1),
            multibagger_base_score=round(multibagger_base_score, 1), forensic_score=round(forensic_score, 1),
            swing_score=swing_score, positional_score=positional_score, multibagger_score=multibagger_score,
            primary_portfolio=primary_portfolio, confluence_tag=confluence_tag, passed_forensic_shield=is_clean,
            has_fundamental_data=has_fundamental_data
        )
