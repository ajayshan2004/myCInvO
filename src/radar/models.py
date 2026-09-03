"""
Module: src/radar/models.py
Purpose: Domain models for screener candidates, trend scores, and breakout setups.
"""
from dataclasses import dataclass
from datetime import date


@dataclass
class ScreenerCandidate:
    """Represents a security meeting quantitative screening criteria."""
    isin: str
    symbol: str
    exchange: str
    company_name: str
    trade_date: date
    close_price: float
    dma_50: float
    dma_150: float
    dma_200: float
    dma_200_slope_positive: bool
    high_52w: float
    low_52w: float
    within_52w_high_pct: float
    above_52w_low_pct: float
    delivery_pct: float
    avg_deliv_volume_50: float
    deliv_volume_multiplier: float
    trend_score: float
