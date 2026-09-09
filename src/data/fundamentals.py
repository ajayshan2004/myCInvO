"""
Module: src/data/fundamentals.py
Purpose: Analyzes CANSLIM quarterly earnings acceleration, Smart Money institutional trends, and Forensic Shield.
"""
from datetime import date
from typing import Any, Dict, List, Optional
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import QuarterlyFinancial, ShareholdingPattern


class FundamentalsEngine:
    """Evaluates CANSLIM fundamental growth, institutional ownership, and forensic risk filters."""

    def __init__(
        self, db_manager: Optional[DuckDBManager] = None, config_manager: Optional[ConfigManager] = None
    ) -> None:
        self.db = db_manager or DuckDBManager()
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.get_config()

    def calculate_earnings_acceleration(self, isin: str) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Query recent quarterly financials for isin ordered by period_end DESC.
        2. Calculate latest YoY PAT growth, Sales growth, and OPM expansion in bps.
        3. Check consecutive quarters of acceleration against configured thresholds.
        4. Return dictionary with growth metrics, acceleration flag, and earnings_score (0-100).
        """
        query = """
            SELECT period_end, sales_cr, operating_profit_cr, opm_pct, net_profit_cr,
                   pat_growth_yoy, sales_growth_yoy, eps
            FROM quarterly_financials WHERE isin = ? ORDER BY period_end DESC LIMIT 6;
        """
        rows = self.db.conn.execute(query, (isin,)).fetchall()
        if not rows:
            return {
                "has_data": False, "pat_growth_yoy": 0.0, "sales_growth_yoy": 0.0,
                "opm_pct": 0.0, "opm_expansion_bps": 0, "is_accelerating": False, "earnings_score": 0.0
            }

        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else latest
        opm_expansion = int(round((latest[3] - prev[3]) * 100))

        pat_growth = latest[5]
        sales_growth = latest[6]
        min_pat = self.config.multibagger_portfolio.min_pat_growth_yoy_pct
        min_sales = self.config.multibagger_portfolio.min_sales_growth_yoy_pct
        min_opm = self.config.multibagger_portfolio.min_opm_expansion_bps

        is_accel = (pat_growth >= min_pat) and (sales_growth >= min_sales)

        # Composite Earnings Score (0 - 100)
        score = 0.0
        if pat_growth >= min_pat:
            score += min(50.0, (pat_growth / 50.0) * 50.0)
        if sales_growth >= min_sales:
            score += min(30.0, (sales_growth / 30.0) * 30.0)
        if opm_expansion >= min_opm:
            score += min(20.0, (opm_expansion / 300.0) * 20.0)

        return {
            "has_data": True, "period_end": latest[0], "pat_growth_yoy": round(pat_growth, 2),
            "sales_growth_yoy": round(sales_growth, 2), "opm_pct": round(latest[3], 2),
            "opm_expansion_bps": opm_expansion, "is_accelerating": is_accel, "earnings_score": min(100.0, round(score, 1))
        }

    def calculate_smart_money_trend(self, isin: str) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Query latest shareholding patterns ordered by period_end DESC.
        2. Calculate QoQ institutional change (FII% + DII% delta).
        3. Check promoter pledge % and retail count trend (shakeout vs absorption).
        4. Return dictionary with ownership footprint and smart_money_score (0-100).
        """
        query = """
            SELECT period_end, promoter_pct, fii_pct, dii_pct, public_retail_pct,
                   pledged_pct, retail_shareholders_count
            FROM shareholding_patterns WHERE isin = ? ORDER BY period_end DESC LIMIT 5;
        """
        rows = self.db.conn.execute(query, (isin,)).fetchall()
        if not rows:
            return {
                "has_data": False, "inst_holding": 0.0, "inst_delta_qoq": 0.0,
                "promoter_pct": 0.0, "pledged_pct": 0.0, "is_accumulating": False, "smart_money_score": 0.0
            }

        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else latest

        inst_latest = latest[2] + latest[3]
        inst_prev = prev[2] + prev[3]
        inst_delta = round(inst_latest - inst_prev, 2)
        pledged = latest[5]
        max_pledge = self.config.multibagger_portfolio.max_promoter_pledge_pct

        is_acc = (inst_delta > 0.0) and (pledged <= max_pledge)

        # Smart money score (0 - 100)
        score = min(40.0, (inst_latest / 40.0) * 40.0)  # Base institutional presence
        if inst_delta > 0:
            score += min(35.0, (inst_delta / 2.0) * 35.0)  # QoQ expansion
        if pledged == 0.0:
            score += 25.0  # Zero pledge bonus
        elif pledged <= max_pledge:
            score += 10.0

        return {
            "has_data": True, "period_end": latest[0], "inst_holding": round(inst_latest, 2),
            "inst_delta_qoq": inst_delta, "promoter_pct": round(latest[1], 2),
            "pledged_pct": round(pledged, 2), "is_accumulating": is_acc, "smart_money_score": min(100.0, round(score, 1))
        }

    def evaluate_forensic_shield(
        self, isin: str, debt_to_equity: float = 0.0, is_asm_gsm: bool = False, market_cap_cr: float = 500.0
    ) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Evaluate promoter pledge, leverage (D/E), market cap, and exchange surveillance against config.
        2. Return pass/fail flags and composite forensic shield health score (0-100).
        """
        f_cfg = self.config.forensic_shield
        row = self.db.conn.execute(
            "SELECT pledged_pct FROM shareholding_patterns WHERE isin = ? ORDER BY period_end DESC LIMIT 1;", (isin,)
        ).fetchone()
        pledged = row[0] if row else 0.0

        pass_pledge = pledged <= f_cfg.max_promoter_pledge_pct
        pass_debt = debt_to_equity <= f_cfg.max_debt_to_equity
        pass_mcap = market_cap_cr >= f_cfg.min_market_cap_cr
        pass_surveillance = not (f_cfg.exclude_asm_gsm and is_asm_gsm)

        is_clean = pass_pledge and pass_debt and pass_mcap and pass_surveillance
        score = (25.0 if pass_pledge else 0.0) + (25.0 if pass_debt else 0.0) + (25.0 if pass_mcap else 0.0) + (25.0 if pass_surveillance else 0.0)

        return {
            "is_clean": is_clean, "forensic_score": score,
            "pass_pledge": pass_pledge, "pass_debt": pass_debt,
            "pass_mcap": pass_mcap, "pass_surveillance": pass_surveillance,
            "pledged_pct": round(pledged, 2), "debt_to_equity": round(debt_to_equity, 2)
        }
