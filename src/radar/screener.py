"""
Module: src/radar/screener.py
Purpose: Vectorized Minervini Stage-2 and institutional delivery screener engine.
"""
from datetime import date
from typing import List, Optional
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager
from src.radar.models import ScreenerCandidate


class RadarScreener:
    """Executes high-throughput vectorized screening queries on DuckDB data lake."""

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
        """Observer callback when rules_engine.yaml changes."""
        self.config = new_config

    def screen_stage2(self, as_of_date: Optional[date] = None) -> List[ScreenerCandidate]:
        """
        PSEUDOCODE:
        1. Resolve as_of_date to latest date in eod_quotes if None.
        2. Query DuckDB using Window functions for 50, 150, 200 DMA, 52W High/Low, and 200-DMA slope.
        3. Filter active equities meeting Minervini Stage 2 criteria and 52W bounds.
        4. Calculate composite trend score and return sorted candidates.
        """
        target_date = as_of_date
        if target_date is None:
            row = self.db.conn.execute("SELECT MAX(trade_date) FROM eod_quotes;").fetchone()
            if not row or not row[0]:
                return []
            target_date = row[0]

        pos_cfg = self.config.positional_portfolio
        within_high_pct = pos_cfg.within_52w_high_pct
        above_low_pct = pos_cfg.min_above_52w_low_pct

        query = """
            WITH ranked_quotes AS (
                SELECT 
                    q.isin, q.symbol, q.exchange, q.trade_date, q.close_price, q.total_volume,
                    q.deliverable_volume, q.delivery_pct, s.company_name, s.is_active, s.instrument_type,
                    AVG(q.close_price) OVER w50 AS dma_50,
                    AVG(q.close_price) OVER w150 AS dma_150,
                    AVG(q.close_price) OVER w200 AS dma_200,
                    AVG(q.close_price) OVER w200_prev AS dma_200_prev,
                    MAX(q.high_price) OVER w252 AS high_52w,
                    MIN(q.low_price) OVER w252 AS low_52w,
                    AVG(q.deliverable_volume) OVER w50 AS avg_deliv_vol_50
                FROM eod_quotes q
                JOIN securities s ON q.isin = s.isin
                WINDOW 
                    w50 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW),
                    w150 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 149 PRECEDING AND CURRENT ROW),
                    w200 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 199 PRECEDING AND CURRENT ROW),
                    w200_prev AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 219 PRECEDING AND 20 PRECEDING),
                    w252 AS (PARTITION BY q.isin ORDER BY q.trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
            )
            SELECT 
                isin, symbol, exchange, company_name, trade_date, close_price,
                dma_50, dma_150, dma_200, (dma_200 > dma_200_prev) as dma_200_slope_pos,
                high_52w, low_52w,
                ROUND((1.0 - (close_price / high_52w)) * 100.0, 2) as within_52w_high_pct,
                ROUND(((close_price / low_52w) - 1.0) * 100.0, 2) as above_52w_low_pct,
                delivery_pct, avg_deliv_vol_50,
                CASE WHEN avg_deliv_vol_50 > 0 THEN ROUND(deliverable_volume / avg_deliv_vol_50, 2) ELSE 1.0 END as deliv_mult
            FROM ranked_quotes
            WHERE trade_date = ?
              AND is_active = TRUE
              AND instrument_type = 'EQUITY'
              AND close_price > dma_50
              AND dma_50 > dma_150
              AND dma_150 > dma_200
              AND dma_200 > dma_200_prev
              AND close_price >= (1.0 - (? / 100.0)) * high_52w
              AND close_price >= (1.0 + (? / 100.0)) * low_52w
            ORDER BY (close_price / dma_50) DESC;
        """
        rows = self.db.conn.execute(query, (target_date, within_high_pct, above_low_pct)).fetchall()
        candidates: List[ScreenerCandidate] = []

        for r in rows:
            # Composite trend score (0 to 100): Weighted proximity to 52W high + moving average momentum
            dist_high = r[12]
            deliv_mult = r[16]
            score = round(max(0.0, min(100.0, (100.0 - dist_high * 2.0) + min(20.0, deliv_mult * 5.0))), 2)

            candidates.append(ScreenerCandidate(
                isin=r[0], symbol=r[1], exchange=r[2], company_name=r[3], trade_date=r[4],
                close_price=r[5], dma_50=round(r[6], 2), dma_150=round(r[7], 2), dma_200=round(r[8], 2),
                dma_200_slope_positive=bool(r[9]), high_52w=r[10], low_52w=r[11],
                within_52w_high_pct=r[12], above_52w_low_pct=r[13],
                delivery_pct=r[14], avg_deliv_volume_50=round(r[15], 2),
                deliv_volume_multiplier=deliv_mult, trend_score=score
            ))

        return sorted(candidates, key=lambda c: c.trend_score, reverse=True)

    def close(self) -> None:
        """Close database connection."""
        self.db.close()
