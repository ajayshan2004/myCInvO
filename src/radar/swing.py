"""
Module: src/radar/swing.py
Purpose: Swing portfolio screener detecting High-Tight Flags, Pocket Pivots, and EMA pullbacks.
"""
from datetime import date
from typing import List, Optional
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange
from src.radar.models import SwingCandidate, SwingSetupType


class SwingScreener:
    """Specialized screener for short-term swing setups (3 to 20-day horizon)."""

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

    def detect_high_tight_flag(
        self, isin: str, symbol: str, company_name: str, quotes: List[EODQuote]
    ) -> Optional[SwingCandidate]:
        """
        PSEUDOCODE:
        1. Check if stock rallied >= min_rally_pct in <= max_rally_days.
        2. Verify subsequent pullback <= max_flag_pullback_pct over 4-8 bars.
        3. Calculate stop-loss (4.5%), entry trigger (flag high), and target (+10%).
        4. Return SwingCandidate if valid HTF setup.
        """
        if len(quotes) < 20:
            return None

        window = quotes[-30:] if len(quotes) >= 30 else quotes
        sw_cfg = self.config.swing_portfolio
        min_rally = sw_cfg.min_rally_pct / 100.0
        max_pb = sw_cfg.max_flag_pullback_pct / 100.0

        # Rally phase (lookback bars excluding last 4 flag bars)
        rally_slice = window[:-4]
        if len(rally_slice) < 10:
            return None

        low_p = min(q.low_price for q in rally_slice)
        peak_p = max(q.high_price for q in rally_slice)
        if low_p <= 0 or ((peak_p - low_p) / low_p) < min_rally:
            return None

        # Flag phase (last 4-6 bars)
        flag_slice = window[-4:]
        flag_low = min(q.low_price for q in flag_slice)
        pullback = (peak_p - flag_low) / peak_p
        if pullback > max_pb:
            return None

        latest = window[-1]
        stop_p = round(latest.close_price * (1.0 - sw_cfg.initial_stop_loss_pct / 100.0), 2)
        target_p = round(latest.close_price * (1.0 + sw_cfg.profit_target_tranche1_pct / 100.0), 2)
        risk = max(0.01, latest.close_price - stop_p)
        reward = max(0.01, target_p - latest.close_price)
        rr = round(reward / risk, 2)

        return SwingCandidate(
            isin=isin, symbol=symbol, company_name=company_name, trade_date=latest.trade_date,
            setup_type=SwingSetupType.HIGH_TIGHT_FLAG, close_price=latest.close_price,
            entry_trigger_price=peak_p, stop_loss_price=stop_p, profit_target_price=target_p,
            risk_reward_ratio=rr, score=90.0
        )

    def detect_pocket_pivot(
        self, isin: str, symbol: str, company_name: str, quotes: List[EODQuote]
    ) -> Optional[SwingCandidate]:
        """
        PSEUDOCODE:
        1. Check if latest day is a positive close (close > prev_close).
        2. Identify highest volume on any down-day in the prior 10 sessions.
        3. If latest volume >= multiplier * highest down volume, qualify Pocket Pivot.
        4. Return SwingCandidate with computed trade levels.
        """
        if len(quotes) < 11:
            return None

        latest = quotes[-1]
        if latest.close_price <= latest.prev_close:
            return None

        prior_10 = quotes[-11:-1]
        down_vols = [q.total_volume for q in prior_10 if q.close_price < q.prev_close]
        max_down_vol = max(down_vols) if down_vols else 1

        sw_cfg = self.config.swing_portfolio
        if latest.total_volume < max_down_vol * sw_cfg.pocket_pivot_volume_mult:
            return None

        stop_p = round(latest.close_price * (1.0 - sw_cfg.initial_stop_loss_pct / 100.0), 2)
        target_p = round(latest.close_price * (1.0 + sw_cfg.profit_target_tranche1_pct / 100.0), 2)
        rr = round((target_p - latest.close_price) / max(0.01, latest.close_price - stop_p), 2)

        return SwingCandidate(
            isin=isin, symbol=symbol, company_name=company_name, trade_date=latest.trade_date,
            setup_type=SwingSetupType.POCKET_PIVOT, close_price=latest.close_price,
            entry_trigger_price=latest.close_price, stop_loss_price=stop_p, profit_target_price=target_p,
            risk_reward_ratio=rr, score=85.0
        )

    def scan_universe(self, as_of_date: Optional[date] = None, limit: int = 50) -> List[SwingCandidate]:
        """
        PSEUDOCODE:
        1. Query top active equities with high delivery or volume.
        2. For each security, fetch recent 30-day quote series.
        3. Evaluate HTF, Pocket Pivot, and EMA pullback setups.
        4. Return sorted list of swing candidates.
        """
        query_date = as_of_date
        if query_date is None:
            r = self.db.conn.execute("SELECT MAX(trade_date) FROM eod_quotes;").fetchone()
            if not r or not r[0]:
                return []
            query_date = r[0]

        active_secs = self.db.conn.execute("""
            SELECT q.isin, q.symbol, s.company_name
            FROM eod_quotes q
            JOIN securities s ON q.isin = s.isin
            WHERE q.trade_date = ? AND s.is_active = TRUE AND s.instrument_type = 'EQUITY'
            ORDER BY q.total_volume DESC
            LIMIT ?;
        """, (query_date, limit * 3)).fetchall()

        candidates: List[SwingCandidate] = []
        for isin, sym, comp in active_secs:
            rows = self.db.conn.execute("""
                SELECT isin, symbol, exchange, trade_date, open_price, high_price,
                       low_price, close_price, prev_close, total_volume, deliverable_volume, delivery_pct
                FROM eod_quotes
                WHERE isin = ?
                ORDER BY trade_date DESC
                LIMIT 30;
            """, (isin,)).fetchall()
            if len(rows) < 15:
                continue

            quotes = [
                EODQuote(
                    isin=r[0], symbol=r[1], exchange=Exchange(r[2]), trade_date=r[3],
                    open_price=r[4], high_price=r[5], low_price=r[6], close_price=r[7],
                    prev_close=r[8], total_volume=r[9], deliverable_volume=r[10], delivery_pct=r[11]
                ) for r in reversed(rows)
            ]

            htf = self.detect_high_tight_flag(isin, sym, comp, quotes)
            if htf:
                candidates.append(htf)
                continue

            pp = self.detect_pocket_pivot(isin, sym, comp, quotes)
            if pp:
                candidates.append(pp)

        return sorted(candidates, key=lambda c: c.score, reverse=True)[:limit]
