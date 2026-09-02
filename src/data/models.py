"""
Module: src/data/models.py
Purpose: Core domain models for security master and daily market quotes.
"""
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class ListingStatus(str, Enum):
    NSE_ONLY = "NSE_ONLY"
    BSE_ONLY = "BSE_ONLY"
    DUAL_LISTED = "DUAL_LISTED"


@dataclass(frozen=True)
class Security:
    """Master equity entity using ISIN as universal identifier."""
    isin: str
    company_name: str
    listing_status: ListingStatus
    nse_symbol: Optional[str] = None
    bse_code: Optional[str] = None
    bse_scrip_id: Optional[str] = None
    industry: Optional[str] = None
    face_value: float = 10.0
    is_active: bool = True

    def __post_init__(self) -> None:
        """
        PSEUDOCODE:
        1. Validate ISIN is a valid 12-character alphanumeric code.
        2. Ensure at least one exchange identifier (NSE symbol or BSE code) is present.
        """
        if not self.isin or len(self.isin) != 12:
            raise ValueError(f"Invalid ISIN '{self.isin}': must be 12 chars.")
        if not self.nse_symbol and not self.bse_code:
            raise ValueError("Security requires at least NSE symbol or BSE code.")


@dataclass(frozen=True)
class EODQuote:
    """Daily market quote and deliverable volume metrics."""
    isin: str
    symbol: str
    exchange: Exchange
    trade_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    prev_close: float
    total_volume: int
    deliverable_volume: int = 0
    delivery_pct: float = 0.0

    def __post_init__(self) -> None:
        """
        PSEUDOCODE:
        1. Validate prices are non-negative and high >= low.
        2. Validate total_volume >= deliverable_volume >= 0.
        3. Constrain delivery_pct to the range [0.0, 100.0].
        """
        if self.high_price < self.low_price or self.low_price < 0:
            raise ValueError(f"Invalid range: High={self.high_price}, Low={self.low_price}")
        if self.deliverable_volume > self.total_volume and self.total_volume > 0:
            raise ValueError("Deliverable volume cannot exceed total volume.")
        if not (0.0 <= self.delivery_pct <= 100.0):
            raise ValueError(f"Delivery pct {self.delivery_pct} must be within [0, 100].")


