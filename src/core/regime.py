"""
Module: src/core/regime.py
Purpose: Market Regime Index (MRI) calculation and adaptive capital allocation engine.
"""
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager


class MarketRegimeType(str, Enum):
    """Macro and market breadth regime classifications."""
    CONFIRMED_BULL = "CONFIRMED_BULL"
    MARKET_UNDER_PRESSURE = "MARKET_UNDER_PRESSURE"
    CORRECTION_BEAR = "CORRECTION_BEAR"


@dataclass
class CapitalAllocation:
    """Recommended portfolio capital routing weights."""
    swing_pct: float
    positional_pct: float
    multibagger_pct: float
    cash_pct: float


@dataclass
class MarketRegimeReport:
    """Comprehensive market breadth and regime diagnostic report."""
    trade_date: date
    mri_score: float
    regime: MarketRegimeType
    pct_above_50_dma: float
    pct_above_200_dma: float
    new_52w_highs: int
    new_52w_lows: int
    net_52w_highs: int
    allocation: CapitalAllocation
    guidance_text: str


class MarketRegimeEngine:
    """Measures whole-market breadth and dynamically manages capital exposure."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        config_manager: Optional[ConfigManager] = None
    ) -> None:
        self.db = db_manager or DuckDBManager(read_only=True)
        self.config_mgr = config_manager or ConfigManager()
        self.config: RulesConfig = self.config_mgr.get_config()
        self.config_mgr.subscribe(self._on_config_change)

    def _on_config_change(self, new_config: RulesConfig) -> None:
        """Observer callback for config hot-reload."""
        self.config = new_config

    def calculate_regime(self, as_of_date: Optional[date] = None) -> MarketRegimeReport:
        """
        PSEUDOCODE:
        1. Resolve as_of_date to latest date in eod_quotes if None.
        2. Query DuckDB for universe breadth (% > 50-DMA, % > 200-DMA, 52W Highs/Lows).
        3. Compute composite MRI score (0 to 100).
        4. Classify regime against bull/neutral thresholds and determine capital allocation.
        5. Return populated MarketRegimeReport.
        """
        target_date = as_of_date
        if target_date is None:
            r = self.db.conn.execute("SELECT MAX(trade_date) FROM eod_quotes;").fetchone()
            if not r or not r[0]:
                raise ValueError("No quotes available in database to compute regime.")
            target_date = r[0]

        query = """
            WITH stats AS (
                SELECT 
                    q.isin, q.trade_date, q.close_price,
                    AVG(q.close_price) OVER w50 AS dma_50,
                    AVG(q.close_price) OVER w200 AS dma_200,
                    MAX(q.high_price) OVER w252 AS high_52w,
                    MIN(q.low_price) OVER w252 AS low_52w
                FROM eod_quotes q
                JOIN securities s ON q.isin = s.isin
                WHERE s.is_active = TRUE AND s.instrument_type = 'EQUITY'
                WINDOW 
                    w50 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW),
                    w200 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW),
                    w252 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
            ),
            latest AS (
                SELECT * FROM stats WHERE trade_date = ?
            )
            SELECT 
                COUNT(*) as total_active,
                SUM(CASE WHEN close_price > dma_50 THEN 1 ELSE 0 END) as above_50,
                SUM(CASE WHEN close_price > dma_200 THEN 1 ELSE 0 END) as above_200,
                SUM(CASE WHEN close_price >= high_52w * 0.98 THEN 1 ELSE 0 END) as near_52w_high,
                SUM(CASE WHEN close_price <= low_52w * 1.02 THEN 1 ELSE 0 END) as near_52w_low
            FROM latest;
        """
        row = self.db.conn.execute(query, (target_date,)).fetchone()
        total, abv_50, abv_200, n_high, n_low = row or (1, 0, 0, 0, 0)
        total = max(1, total)

        pct_50 = round((abv_50 / total) * 100.0, 2)
        pct_200 = round((abv_200 / total) * 100.0, 2)
        net_highs = (n_high or 0) - (n_low or 0)

        # Composite MRI formula: 45% short-term + 45% long-term + 10% 52W net expansion
        mri_raw = (pct_50 * 0.45) + (pct_200 * 0.45) + min(10.0, max(-10.0, net_highs / 20.0))
        mri_score = round(max(0.0, min(100.0, mri_raw)), 2)

        regime_cfg = self.config.market_regime
        if mri_score >= regime_cfg.bull_threshold:
            regime = MarketRegimeType.CONFIRMED_BULL
            alloc = CapitalAllocation(swing_pct=25.0, positional_pct=45.0, multibagger_pct=30.0, cash_pct=0.0)
            guidance = "CONFIRMED BULL: Full offensive stance. Aggressively deploy capital into valid base breakouts."
        elif mri_score >= regime_cfg.neutral_threshold:
            regime = MarketRegimeType.MARKET_UNDER_PRESSURE
            alloc = CapitalAllocation(swing_pct=10.0, positional_pct=30.0, multibagger_pct=25.0, cash_pct=35.0)
            guidance = "MARKET UNDER PRESSURE: Defensive posture. Only trade A+ setups, sit in 35% cash, tighten stops to 4-5%."
        else:
            regime = MarketRegimeType.CORRECTION_BEAR
            alloc = CapitalAllocation(swing_pct=0.0, positional_pct=10.0, multibagger_pct=20.0, cash_pct=70.0)
            guidance = "CORRECTION / BEAR: Capital preservation mode. 70% in Liquid Cash/ETFs, zero new swing positions."

        return MarketRegimeReport(
            trade_date=target_date, mri_score=mri_score, regime=regime,
            pct_above_50_dma=pct_50, pct_above_200_dma=pct_200,
            new_52w_highs=n_high or 0, new_52w_lows=n_low or 0, net_52w_highs=net_highs,
            allocation=alloc, guidance_text=guidance
        )
