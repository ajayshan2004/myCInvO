"""
Module: src/radar/models.py
Purpose: Domain models for screener candidates, trend scores, and breakout setups.
"""
from dataclasses import dataclass
from datetime import date
from enum import Enum


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


@dataclass
class Contraction:
    """Represents a single contraction wave within a base."""
    depth_pct: float
    duration_bars: int
    peak_price: float
    trough_price: float


@dataclass
class VCPPattern:
    """Represents a Volatility Contraction Pattern (VCP) setup."""
    isin: str
    symbol: str
    company_name: str
    base_length_bars: int
    contractions: list[Contraction]
    pivot_price: float
    final_contraction_depth_pct: float
    volume_dryup_detected: bool
    is_valid_vcp: bool
    breakout_detected: bool


class SwingSetupType(str, Enum):
    """Types of swing trading momentum and pullback setups."""
    HIGH_TIGHT_FLAG = "HIGH_TIGHT_FLAG"
    POCKET_PIVOT = "POCKET_PIVOT"
    EMA_PULLBACK = "EMA_PULLBACK"


@dataclass
class SwingCandidate:
    """Represents a qualified swing trade candidate with trade parameters."""
    isin: str
    symbol: str
    company_name: str
    trade_date: date
    setup_type: SwingSetupType
    close_price: float
    entry_trigger_price: float
    stop_loss_price: float
    profit_target_price: float
    risk_reward_ratio: float
    score: float


@dataclass
class MultibaggerCandidate:
    """Represents a multi-year base breakout or structural compounder candidate."""
    isin: str
    symbol: str
    company_name: str
    trade_date: date
    close_price: float
    multi_year_high: float
    base_duration_months: int
    ath_breakout: bool
    weekly_30_ema: float
    volume_surge_mult: float
    trend_strength_score: float



