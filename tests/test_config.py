"""
Module: tests/test_config.py
Purpose: Unit tests for dynamic configuration loading, validation, hot-reloading, and observers.
"""
from pathlib import Path
import pytest
from src.core.config import ConfigManager, RulesConfig


def test_default_config_fallback(tmp_path):
    """Verify fallback to default configuration when file does not exist."""
    fake_path = tmp_path / "non_existent.yaml"
    manager = ConfigManager(str(fake_path))
    config = manager.get_config()
    assert isinstance(config, RulesConfig)
    assert config.market_regime.bull_threshold == 75.0
    assert config.swing_portfolio.initial_stop_loss_pct == 4.5
    assert config.positional_portfolio.delivery_volume_multiplier == 2.0
    assert config.multibagger_portfolio.min_base_duration_months == 24


def test_yaml_config_loading():
    """Verify that config/rules_engine.yaml loads correctly with all tiers."""
    manager = ConfigManager("config/rules_engine.yaml")
    config = manager.get_config()

    # Tier 1: Swing
    assert config.swing_portfolio.enabled is True
    assert config.swing_portfolio.min_rally_pct == 40.0
    assert config.swing_portfolio.max_flag_pullback_pct == 12.0

    # Tier 2: Positional
    assert config.positional_portfolio.enabled is True
    assert config.positional_portfolio.minervini_200_dma_slope_days == 20
    assert config.positional_portfolio.pyramid_tranches == [0.50, 0.30, 0.20]

    # Tier 3: Multibagger
    assert config.multibagger_portfolio.min_pat_growth_yoy_pct == 25.0
    assert config.multibagger_portfolio.trailing_stop_weekly_ema == 30

    # Risk & Forensics
    assert config.risk_management.max_risk_per_trade_pct == 1.0
    assert config.forensic_shield.max_debt_to_equity == 1.5


def test_validation_boundaries(tmp_path):
    """Verify that invalid numeric boundaries raise ValueError."""
    bad_yaml = tmp_path / "bad_rules.yaml"
    bad_yaml.write_text("swing_portfolio:\n  initial_stop_loss_pct: -5.0\n")
    with pytest.raises(ValueError, match="Swing initial_stop_loss_pct"):
        ConfigManager(str(bad_yaml))


def test_hot_reloading_and_subscribers(tmp_path):
    """Verify hot-reloading when YAML file changes on disk and listener notification."""
    config_file = tmp_path / "dynamic_rules.yaml"
    config_file.write_text("swing_portfolio:\n  min_rally_pct: 35.0\n")

    manager = ConfigManager(str(config_file))
    assert manager.get_config().swing_portfolio.min_rally_pct == 35.0

    notifications = []
    manager.subscribe(lambda cfg: notifications.append(cfg.swing_portfolio.min_rally_pct))

    # Without file change -> returns False
    assert manager.reload_if_changed() is False
    assert len(notifications) == 0

    # Update file content on disk
    config_file.write_text("swing_portfolio:\n  min_rally_pct: 50.0\n")
    reloaded = manager.reload_if_changed()
    assert reloaded is True
    assert manager.get_config().swing_portfolio.min_rally_pct == 50.0
    assert notifications == [50.0]
