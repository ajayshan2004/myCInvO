"""
Module: src/core/config.py
Purpose: Type-safe dynamic configuration manager with hot-reloading and observer pattern.
"""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from typing import Callable, List, Optional
import yaml


@dataclass
class MarketRegimeConfig:
    bull_threshold: float = 75.0
    neutral_threshold: float = 45.0
    lookback_days: int = 252
    vix_elevated_threshold: float = 18.0
    vix_extreme_threshold: float = 24.0


@dataclass
class SwingPortfolioConfig:
    enabled: bool = True
    min_rally_pct: float = 40.0
    max_rally_days: int = 20
    max_flag_pullback_pct: float = 12.0
    flag_volume_dryup_ratio: float = 0.40
    pocket_pivot_volume_mult: float = 1.25
    initial_stop_loss_pct: float = 4.5
    profit_target_tranche1_pct: float = 10.0
    max_holding_days: int = 15


@dataclass
class PositionalPortfolioConfig:
    enabled: bool = True
    minervini_200_dma_slope_days: int = 20
    min_above_52w_low_pct: float = 30.0
    within_52w_high_pct: float = 15.0
    mansfield_rs_threshold: float = 5.0
    vcp_max_contractions: int = 4
    vcp_final_contraction_max_pct: float = 6.0
    delivery_volume_multiplier: float = 2.0
    initial_stop_loss_pct: float = 7.0
    pyramid_tranches: List[float] = field(default_factory=lambda: [0.50, 0.30, 0.20])


@dataclass
class MultibaggerPortfolioConfig:
    enabled: bool = True
    min_base_duration_months: int = 24
    min_pat_growth_yoy_pct: float = 25.0
    min_sales_growth_yoy_pct: float = 20.0
    min_opm_expansion_bps: int = 150
    min_roce_pct: float = 18.0
    max_promoter_pledge_pct: float = 10.0
    trailing_stop_weekly_ema: int = 30


@dataclass
class ForensicShieldConfig:
    min_market_cap_cr: float = 100.0
    max_debt_to_equity: float = 1.5
    min_cfo_to_ebitda: float = 0.70
    max_promoter_pledge_pct: float = 15.0
    exclude_asm_gsm: bool = True


@dataclass
class RiskManagementConfig:
    max_risk_per_trade_pct: float = 1.0
    max_portfolio_heat_pct: float = 6.0
    max_single_stock_allocation_pct: float = 15.0
    max_single_sector_allocation_pct: float = 25.0


@dataclass
class RulesConfig:
    market_regime: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    swing_portfolio: SwingPortfolioConfig = field(default_factory=SwingPortfolioConfig)
    positional_portfolio: PositionalPortfolioConfig = field(default_factory=PositionalPortfolioConfig)
    multibagger_portfolio: MultibaggerPortfolioConfig = field(default_factory=MultibaggerPortfolioConfig)
    forensic_shield: ForensicShieldConfig = field(default_factory=ForensicShieldConfig)
    risk_management: RiskManagementConfig = field(default_factory=RiskManagementConfig)


class ConfigManager:
    """Centralized dynamic rules configuration manager with observer hot-reloading."""

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path = Path(config_path or "config/rules_engine.yaml")
        self._listeners: List[Callable[[RulesConfig], None]] = []
        self._last_hash: Optional[str] = None
        self._cached_config: RulesConfig = self.load_config()

    def subscribe(self, callback: Callable[[RulesConfig], None]) -> None:
        """
        PSEUDOCODE:
        1. Append callback to listeners list.
        """
        self._listeners.append(callback)

    def load_config(self) -> RulesConfig:
        """
        PSEUDOCODE:
        1. If YAML file exists, read content and compute MD5 hash.
        2. Parse sections and instantiate strongly typed dataclasses.
        3. Validate numeric boundaries (positive stop losses, positive periods).
        4. Return validated RulesConfig instance.
        """
        if not self.config_path.exists():
            return RulesConfig()

        content = self.config_path.read_bytes()
        self._last_hash = hashlib.md5(content).hexdigest()
        raw = yaml.safe_load(content.decode("utf-8")) or {}

        config = RulesConfig(
            market_regime=MarketRegimeConfig(**raw.get("market_regime", {})),
            swing_portfolio=SwingPortfolioConfig(**raw.get("swing_portfolio", {})),
            positional_portfolio=PositionalPortfolioConfig(**raw.get("positional_portfolio", {})),
            multibagger_portfolio=MultibaggerPortfolioConfig(**raw.get("multibagger_portfolio", {})),
            forensic_shield=ForensicShieldConfig(**raw.get("forensic_shield", {})),
            risk_management=RiskManagementConfig(**raw.get("risk_management", {})),
        )
        self._validate_config(config)
        return config

    def reload_if_changed(self) -> bool:
        """
        PSEUDOCODE:
        1. If file does not exist, return False.
        2. Compute current MD5 hash of config file.
        3. If hash differs from _last_hash, reload config and notify all listeners.
        4. Return True if reloaded, False otherwise.
        """
        if not self.config_path.exists():
            return False

        current_hash = hashlib.md5(self.config_path.read_bytes()).hexdigest()
        if current_hash != self._last_hash:
            self._cached_config = self.load_config()
            for listener in self._listeners:
                listener(self._cached_config)
            return True
        return False

    def get_config(self) -> RulesConfig:
        """Return currently cached active configuration."""
        return self._cached_config

    def _validate_config(self, config: RulesConfig) -> None:
        """
        PSEUDOCODE:
        1. Assert initial stop losses are strictly positive and <= 25%.
        2. Assert lookback and moving average periods are >= 1.
        3. Assert risk limits are between 0.1% and 100%.
        """
        if not (0 < config.swing_portfolio.initial_stop_loss_pct <= 25.0):
            raise ValueError("Swing initial_stop_loss_pct must be between 0 and 25%")
        if not (0 < config.positional_portfolio.initial_stop_loss_pct <= 25.0):
            raise ValueError("Positional initial_stop_loss_pct must be between 0 and 25%")
        if config.market_regime.lookback_days < 1:
            raise ValueError("lookback_days must be at least 1")
        if not (0 < config.risk_management.max_risk_per_trade_pct <= 10.0):
            raise ValueError("max_risk_per_trade_pct must be between 0 and 10%")
