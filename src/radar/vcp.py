"""
Module: src/radar/vcp.py
Purpose: Volatility Contraction Pattern (VCP) detection and base geometry engine.
"""
from typing import List, Optional
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager
from src.data.models import EODQuote
from src.radar.models import Contraction, ScreenerCandidate, VCPPattern


class VCPDetector:
    """Algorithmically detects Volatility Contraction Patterns (VCP) and pivot buy points."""

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

    def detect_vcp(self, isin: str, symbol: str, company_name: str, quotes: List[EODQuote]) -> Optional[VCPPattern]:
        """
        PSEUDOCODE:
        1. Ensure quotes list has at least 30 bars, sorted ascending by trade_date.
        2. Identify local peaks and troughs over the lookback window to segment contractions.
        3. Validate monotonic decrease in contraction depth (T1 > T2 > T3).
        4. Validate final contraction depth <= threshold and volume dry-up (VDU).
        5. Calculate pivot high price and detect if latest close triggers a breakout.
        6. Return populated VCPPattern if valid, else None.
        """
        if len(quotes) < 30:
            return None

        # Lookback window (last 30 to 90 bars)
        window = quotes[-90:] if len(quotes) >= 90 else quotes
        n = len(window)

        # 50-day average volume for VDU comparison
        vol_50_slice = window[-50:] if n >= 50 else window
        avg_vol_50 = sum(q.total_volume for q in vol_50_slice) / len(vol_50_slice) if vol_50_slice else 1.0

        # Extract local swing highs and lows (step size = 3)
        peaks: List[int] = []
        troughs: List[int] = []
        for i in range(2, n - 2):
            if window[i].high_price >= max(window[i-2].high_price, window[i-1].high_price, window[i+1].high_price, window[i+2].high_price):
                peaks.append(i)
            if window[i].low_price <= min(window[i-2].low_price, window[i-1].low_price, window[i+1].low_price, window[i+2].low_price):
                troughs.append(i)

        if len(peaks) < 2 or len(troughs) < 2:
            return None

        # Build contraction waves between alternating peaks and subsequent troughs
        contractions: List[Contraction] = []
        for p_idx in peaks:
            # Find next trough occurring after this peak
            next_troughs = [t for t in troughs if t > p_idx]
            if not next_troughs:
                continue
            t_idx = next_troughs[0]
            peak_p = window[p_idx].high_price
            trough_p = min(q.low_price for q in window[p_idx:t_idx+1])
            if peak_p <= 0:
                continue
            depth = ((peak_p - trough_p) / peak_p) * 100.0
            duration = t_idx - p_idx + 1
            if depth > 0.5:
                contractions.append(Contraction(
                    depth_pct=round(depth, 2), duration_bars=duration,
                    peak_price=round(peak_p, 2), trough_price=round(trough_p, 2)
                ))

        if len(contractions) < 2:
            return None

        # Limit to last 2 to 4 contractions
        contractions = contractions[-4:]
        depths = [c.depth_pct for c in contractions]

        # Contraction dampening: verify overall decreasing wave depth
        is_dampening = depths[0] > depths[-1] and all(
            depths[i] >= depths[i+1] * 0.85 for i in range(len(depths) - 1)
        )

        final_depth = depths[-1]
        max_final = self.config.positional_portfolio.vcp_final_contraction_max_pct
        is_tight = final_depth <= (max_final + 2.0)

        # Volume Dry-Up (VDU) in last contraction
        last_c = contractions[-1]
        last_slice = window[-last_c.duration_bars:] if last_c.duration_bars > 0 else window[-5:]
        last_vol_avg = sum(q.total_volume for q in last_slice) / len(last_slice) if last_slice else 0.0
        vdu_detected = last_vol_avg <= (avg_vol_50 * 0.75)

        # Pivot Price: Highest point in the final contraction wave
        pivot_price = last_c.peak_price
        latest_quote = window[-1]
        breakout = (latest_quote.close_price >= pivot_price * 0.99) and (latest_quote.total_volume >= avg_vol_50 * 1.25)
        is_valid = is_dampening and is_tight

        return VCPPattern(
            isin=isin, symbol=symbol, company_name=company_name, base_length_bars=n,
            contractions=contractions, pivot_price=pivot_price,
            final_contraction_depth_pct=final_depth, volume_dryup_detected=vdu_detected,
            is_valid_vcp=is_valid, breakout_detected=breakout
        )

    def scan_candidates(self, candidates: List[ScreenerCandidate], limit: int = 50) -> List[VCPPattern]:
        """
        PSEUDOCODE:
        1. For top N candidates, fetch 90 recent quotes from DuckDB.
        2. Run detect_vcp on each candidate's quote history.
        3. Filter and return valid VCP patterns.
        """
        vcp_patterns: List[VCPPattern] = []
        for cand in candidates[:limit]:
            rows = self.db.conn.execute("""
                SELECT isin, symbol, exchange, trade_date, open_price, high_price,
                       low_price, close_price, prev_close, total_volume, deliverable_volume, delivery_pct
                FROM eod_quotes
                WHERE isin = ?
                ORDER BY trade_date ASC;
            """, (cand.isin,)).fetchall()
            if not rows:
                continue

            from src.data.models import Exchange
            quotes = [
                EODQuote(
                    isin=r[0], symbol=r[1], exchange=Exchange(r[2]), trade_date=r[3],
                    open_price=r[4], high_price=r[5], low_price=r[6], close_price=r[7],
                    prev_close=r[8], total_volume=r[9], deliverable_volume=r[10], delivery_pct=r[11]
                ) for r in rows
            ]
            pattern = self.detect_vcp(cand.isin, cand.symbol, cand.company_name, quotes)
            if pattern and pattern.is_valid_vcp:
                vcp_patterns.append(pattern)

        return vcp_patterns
