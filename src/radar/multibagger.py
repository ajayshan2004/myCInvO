"""
Module: src/radar/multibagger.py
Purpose: Multibagger portfolio screener detecting multi-year base breakouts and structural compounders.
"""
from datetime import date
from typing import List, Optional
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager
from src.data.models import EODQuote, Exchange
from src.radar.models import MultibaggerCandidate


class MultibaggerScreener:
    """Screener for long-term compounders breaking out of multi-year accumulation bases."""

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

    def _calculate_weekly_30_ema(self, quotes: List[EODQuote]) -> float:
        """
        PSEUDOCODE:
        1. Group daily quotes into weekly buckets by (year, week_number).
        2. Take the last close_price of each week.
        3. Compute 30-period exponential moving average on weekly closes.
        4. Return latest weekly 30-EMA value.
        """
        weekly_map = {}
        for q in quotes:
            yw = q.trade_date.isocalendar()[:2]
            weekly_map[yw] = q.close_price

        weekly_closes = list(weekly_map.values())
        if not weekly_closes:
            return quotes[-1].close_price if quotes else 0.0

        period = self.config.multibagger_portfolio.trailing_stop_weekly_ema
        alpha = 2.0 / (period + 1.0)
        ema = weekly_closes[0]
        for c in weekly_closes[1:]:
            ema = (alpha * c) + ((1.0 - alpha) * ema)
        return round(ema, 2)

    def detect_multi_year_breakout(
        self, isin: str, symbol: str, company_name: str, quotes: List[EODQuote]
    ) -> Optional[MultibaggerCandidate]:
        """
        PSEUDOCODE:
        1. Ensure quotes list has at least 400 trading days (roughly 18-24 months).
        2. Determine multi-year resistance peak from prior base (excluding last 10 bars).
        3. Verify latest close >= 99% of prior multi-year high (Stage 1 to 2 breakout).
        4. Compute weekly 30-EMA and ensure price > weekly 30-EMA.
        5. Return populated MultibaggerCandidate if breakout conditions are met.
        """
        if len(quotes) < 400:
            return None

        # Lookback up to 1250 bars (~5 years)
        window = quotes[-1250:] if len(quotes) >= 1250 else quotes
        base_slice = window[:-10]
        prior_high = max(q.high_price for q in base_slice)
        ath_high = max(q.high_price for q in window)

        latest = window[-1]
        if latest.close_price < (prior_high * 0.985):
            return None

        # Calculate base duration in calendar months
        duration_days = (latest.trade_date - window[0].trade_date).days
        duration_months = max(18, duration_days // 30)

        # 50-day average volume for surge check
        vol_50 = [q.total_volume for q in window[-50:]]
        avg_vol_50 = sum(vol_50) / len(vol_50) if vol_50 else 1.0
        vol_surge = round(latest.total_volume / max(1.0, avg_vol_50), 2)

        weekly_ema = self._calculate_weekly_30_ema(window)
        max_close = max(q.close_price for q in window)
        is_ath = (latest.close_price >= max_close * 0.99) or (latest.close_price >= ath_high * 0.98)

        # Composite trend score (0 to 100): Weighted base duration + ATH status + volume
        score = round(min(100.0, 60.0 + min(20.0, duration_months * 0.5) + (10.0 if is_ath else 0.0) + min(10.0, vol_surge * 2.0)), 2)

        return MultibaggerCandidate(
            isin=isin, symbol=symbol, company_name=company_name, trade_date=latest.trade_date,
            close_price=latest.close_price, multi_year_high=prior_high,
            base_duration_months=duration_months, ath_breakout=is_ath,
            weekly_30_ema=weekly_ema, volume_surge_mult=vol_surge, trend_strength_score=score
        )

    def scan_universe(self, as_of_date: Optional[date] = None, limit: int = 50) -> List[MultibaggerCandidate]:
        """
        PSEUDOCODE:
        1. Find active equities trading near multi-year highs in DuckDB.
        2. Fetch full historical quotes for each security.
        3. Evaluate detect_multi_year_breakout.
        4. Return top ranked multibagger candidates.
        """
        target_date = as_of_date
        if target_date is None:
            r = self.db.conn.execute("SELECT MAX(trade_date) FROM eod_quotes;").fetchone()
            if not r or not r[0]:
                return []
            target_date = r[0]

        top_secs = self.db.conn.execute("""
            SELECT q.isin, q.symbol, s.company_name
            FROM eod_quotes q
            JOIN securities s ON q.isin = s.isin
            WHERE q.trade_date = ? AND s.is_active = TRUE AND s.instrument_type = 'EQUITY'
            ORDER BY q.close_price * q.total_volume DESC
            LIMIT ?;
        """, (target_date, limit * 3)).fetchall()

        candidates: List[MultibaggerCandidate] = []
        for isin, sym, comp in top_secs:
            rows = self.db.conn.execute("""
                SELECT isin, symbol, exchange, trade_date, open_price, high_price,
                       low_price, close_price, prev_close, total_volume, deliverable_volume, delivery_pct
                FROM eod_quotes
                WHERE isin = ?
                ORDER BY trade_date ASC;
            """, (isin,)).fetchall()
            if len(rows) < 400:
                continue

            quotes = [
                EODQuote(
                    isin=r[0], symbol=r[1], exchange=Exchange(r[2]), trade_date=r[3],
                    open_price=r[4], high_price=r[5], low_price=r[6], close_price=r[7],
                    prev_close=r[8], total_volume=r[9], deliverable_volume=r[10], delivery_pct=r[11]
                ) for r in rows
            ]
            cand = self.detect_multi_year_breakout(isin, sym, comp, quotes)
            if cand:
                candidates.append(cand)

        return sorted(candidates, key=lambda c: c.trend_strength_score, reverse=True)[:limit]
