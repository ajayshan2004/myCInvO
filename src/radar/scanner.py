"""
Module: src/radar/scanner.py
Purpose: Live Opportunity Scanner orchestrating screens, multi-lens scoring, and institutional report generation.
"""
import argparse
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from src.core.config import ConfigManager
from src.core.regime import MarketRegimeEngine
from src.data.db import DuckDBManager
from src.data.indices import IndexIngestionEngine
from src.data.models import EODQuote, Exchange, IndexQuote
from src.radar.models import OpportunityScore
from src.radar.multibagger import MultibaggerScreener
from src.radar.scoring import PortfolioScoringEngine
from src.radar.screener import RadarScreener
from src.radar.swing import SwingScreener
from src.radar.vcp import VCPDetector


class RadarScanner:
    """End-to-end scanner orchestrating screeners, portfolio scoring, and confluence reporting."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        config_manager: Optional[ConfigManager] = None,
        read_only: bool = True,
    ) -> None:
        self.db = db_manager or DuckDBManager(read_only=read_only)
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.get_config()
        self.regime_engine = MarketRegimeEngine(self.db, self.config_manager)
        self.index_engine = IndexIngestionEngine(self.db, config_manager=self.config_manager)
        self.screener = RadarScreener(self.db, self.config_manager)
        self.vcp_detector = VCPDetector(self.db, self.config_manager)
        self.swing_screener = SwingScreener(self.db, self.config_manager)
        self.multibagger_screener = MultibaggerScreener(self.db, self.config_manager)
        self.scoring_engine = PortfolioScoringEngine(self.db, self.config_manager, read_only=read_only)

    def scan(self, as_of_date: Optional[date] = None, top_n: int = 10) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Calculate current Market Regime and Sector Leadership rankings.
        2. Execute Stage-2, Swing (HTF + Pocket Pivot), and Multibagger screens.
        3. For each candidate, evaluate VCP geometry, Mansfield RS, and granular rule sub-scores.
        4. Group results into Swing, Positional, Multibagger, Unicorns 🦄, and Dual Confluence 🔥.
        5. Return comprehensive scan results dictionary.
        """
        regime = self.regime_engine.calculate_regime(as_of_date)
        sector_rankings = self.index_engine.get_sector_rankings(as_of_date)

        stage2_candidates = self.screener.screen_stage2(as_of_date)
        swing_candidates = self.swing_screener.scan_universe(as_of_date=as_of_date, limit=50)
        mb_candidates = self.multibagger_screener.scan_universe(as_of_date=as_of_date, limit=50)

        # Collect unique ISINs across all screens
        candidates_map: Dict[str, Dict[str, Any]] = {}
        for c in stage2_candidates:
            candidates_map[c.isin] = {
                "isin": c.isin, "symbol": c.symbol, "company_name": c.company_name, "trade_date": c.trade_date,
                "trend_score": c.trend_score, "volume_footprint_score": min(100.0, c.deliv_volume_multiplier * 40.0),
                "multibagger_base_score": 0.0, "is_stage2": True,
            }
        for s in swing_candidates:
            if s.isin not in candidates_map:
                candidates_map[s.isin] = {
                    "isin": s.isin, "symbol": s.symbol, "company_name": s.company_name, "trade_date": s.trade_date,
                    "trend_score": 75.0, "volume_footprint_score": s.score, "multibagger_base_score": 0.0, "is_stage2": False,
                }
            else:
                candidates_map[s.isin]["volume_footprint_score"] = max(candidates_map[s.isin]["volume_footprint_score"], s.score)
        for m in mb_candidates:
            if m.isin not in candidates_map:
                candidates_map[m.isin] = {
                    "isin": m.isin, "symbol": m.symbol, "company_name": m.company_name, "trade_date": m.trade_date,
                    "trend_score": m.trend_strength_score, "volume_footprint_score": 70.0,
                    "multibagger_base_score": min(100.0, (m.base_duration_months / 24.0) * 50.0 + (50.0 if m.ath_breakout else 25.0)),
                    "is_stage2": True,
                }
            else:
                candidates_map[m.isin]["multibagger_base_score"] = min(
                    100.0, (m.base_duration_months / 24.0) * 50.0 + (50.0 if m.ath_breakout else 25.0)
                )

        scored_opportunities: List[OpportunityScore] = []
        for isin, info in candidates_map.items():
            # Fetch recent quotes for VCP analysis
            q_rows = self.db.conn.execute("""
                SELECT isin, symbol, exchange, trade_date, open_price, high_price,
                       low_price, close_price, prev_close, total_volume, deliverable_volume, delivery_pct
                FROM eod_quotes
                WHERE isin = ? AND trade_date <= ?
                ORDER BY trade_date ASC;
            """, (isin, info["trade_date"])).fetchall()

            quotes = [
                EODQuote(
                    isin=r[0], symbol=r[1], exchange=Exchange(r[2]), trade_date=r[3],
                    open_price=r[4], high_price=r[5], low_price=r[6], close_price=r[7],
                    prev_close=r[8], total_volume=r[9], deliverable_volume=r[10], delivery_pct=r[11]
                ) for r in q_rows
            ]

            # Check VCP geometry
            vcp_res = self.vcp_detector.detect_vcp(isin, info["symbol"], info["company_name"], quotes)
            vcp_score = 80.0 if (vcp_res and vcp_res.is_valid_vcp) else (50.0 if (vcp_res and len(vcp_res.contractions) >= 2) else 30.0)
            if vcp_res and vcp_res.volume_dryup_detected:
                vcp_score += 15.0

            # Mansfield RS vs Nifty 50
            mrs = self.index_engine.calculate_mansfield_rs(isin, "Nifty 50")
            momentum_score = min(100.0, max(0.0, 50.0 + (mrs * 2.5)))

            score = self.scoring_engine.calculate_granular_scores(
                isin=isin, symbol=info["symbol"], company_name=info["company_name"], trade_date=info["trade_date"],
                trend_score=info["trend_score"], vcp_score=min(100.0, vcp_score), momentum_score=momentum_score,
                volume_footprint_score=info["volume_footprint_score"], multibagger_base_score=info["multibagger_base_score"]
            )
            scored_opportunities.append(score)

        # Categorization & Sorting
        unicorns = [o for o in scored_opportunities if o.confluence_tag == "Triple Confluence Unicorn 🦄"]
        duals = [o for o in scored_opportunities if o.confluence_tag == "Dual Confluence 🔥"]
        swings = sorted([o for o in scored_opportunities if o.primary_portfolio == "SWING" or o.swing_score >= 70.0], key=lambda x: x.swing_score, reverse=True)[:top_n]
        positionals = sorted([o for o in scored_opportunities if o.primary_portfolio == "POSITIONAL" or o.positional_score >= 70.0], key=lambda x: x.positional_score, reverse=True)[:top_n]
        multibaggers = sorted([o for o in scored_opportunities if o.primary_portfolio == "MULTIBAGGER" or o.multibagger_score >= 70.0], key=lambda x: x.multibagger_score, reverse=True)[:top_n]

        return {
            "regime": regime,
            "sector_rankings": sector_rankings[:5],
            "total_screened": len(candidates_map),
            "unicorns": unicorns,
            "dual_confluence": duals,
            "swing_candidates": swings,
            "positional_candidates": positionals,
            "multibagger_candidates": multibaggers,
        }

    def print_report(self, results: Dict[str, Any]) -> None:
        """Render institutional terminal opportunity report."""
        regime = results["regime"]
        print("\n" + "=" * 95)
        print(f"  ALPHACRAFT INSTITUTIONAL RADAR: MARKET REGIME & OPPORTUNITY REPORT")
        print("=" * 95)
        print(f"  Regime: {regime.regime.value:<22} | MRI Score: {regime.mri_score:>5.1f}/100")
        print(f"  Breadth (>50 DMA): {regime.pct_above_50_dma:>5.1f}% | Breadth (>200 DMA): {regime.pct_above_200_dma:>5.1f}% | Net 52W Highs: {regime.net_52w_highs:>+4d}")
        print(f"  Allocation: Swing: {regime.allocation.swing_pct:.0f}% | Positional: {regime.allocation.positional_pct:.0f}% | Multibagger: {regime.allocation.multibagger_pct:.0f}% | Cash: {regime.allocation.cash_pct:.0f}%")
        print(f"  Guidance: {regime.guidance_text}")
        print("-" * 95)

        sectors = results.get("sector_rankings", [])
        if sectors:
            print("  LEADING SECTOR GROUPS (Top 3 Momentum Leaders):")
            for rank, (name, ret_3m, ret_6m) in enumerate(sectors[:3], 1):
                print(f"    {rank}. {name:<25} | 3M Return: {ret_3m:>+6.2f}% | 6M Return: {ret_6m:>+6.2f}%")
            print("-" * 95)

        if results.get("unicorns"):
            print("  [UNICORN] TRIPLE CONFLUENCE (Swing + Positional + Multibagger >= 75):")
            for u in results["unicorns"]:
                print(f"    * {u.symbol:<12} | Swing: {u.swing_score:>5.1f} | Positional: {u.positional_score:>5.1f} | Multibagger: {u.multibagger_score:>5.1f} | Primary: {u.primary_portfolio}")
            print("-" * 95)

        if results.get("dual_confluence"):
            print("  [DUAL CONFLUENCE] HIGH MOMENTUM (2+ Horizons >= 75):")
            for d in results["dual_confluence"][:5]:
                print(f"    * {d.symbol:<12} | Swing: {d.swing_score:>5.1f} | Positional: {d.positional_score:>5.1f} | Multibagger: {d.multibagger_score:>5.1f} | Primary: {d.primary_portfolio}")
            print("-" * 95)

        print("  TOP POSITIONAL OPPORTUNITIES (1-6 Months | Minervini Stage-2 + VCP Breakout):")
        print(f"    {'Symbol':<12} {'Positional':<12} {'Trend':<8} {'VCP':<8} {'MRS Alpha':<12} {'Forensics'}")
        for p in results.get("positional_candidates", [])[:5]:
            print(f"    {p.symbol:<12} {p.positional_score:>9.1f}  {p.trend_score:>6.1f}  {p.vcp_score:>6.1f}  {p.momentum_score:>10.1f}   {'PASS' if p.passed_forensic_shield else 'FAIL'}")

        print("\n  TOP SWING OPPORTUNITIES (3-20 Days | High-Tight Flags & Pocket Pivots):")
        print(f"    {'Symbol':<12} {'SwingScore':<12} {'Volume Footprint':<18} {'Trend':<8} {'Forensics'}")
        for s in results.get("swing_candidates", [])[:5]:
            print(f"    {s.symbol:<12} {s.swing_score:>9.1f}  {s.volume_footprint_score:>16.1f}  {s.trend_score:>6.1f}   {'PASS' if s.passed_forensic_shield else 'FAIL'}")

        print("\n  TOP MULTIBAGGER OPPORTUNITIES (6M-3Y+ | Multi-Year Rounding Base + CANSLIM):")
        print(f"    {'Symbol':<12} {'MultiScore':<12} {'Base Breakout':<16} {'Earnings':<10} {'Smart Money'}")
        for m in results.get("multibagger_candidates", [])[:5]:
            print(f"    {m.symbol:<12} {m.multibagger_score:>9.1f}  {m.multibagger_base_score:>14.1f}  {m.earnings_score:>8.1f}   {m.smart_money_score:>10.1f}")
        print("=" * 95 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlphaCraft Institutional Market Scanner")
    parser.add_argument("--date", type=str, default=None, help="Scan date (YYYY-MM-DD)")
    parser.add_argument("--top", type=int, default=10, help="Top N opportunities per portfolio")
    args = parser.parse_args()

    as_of = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    scanner = RadarScanner(read_only=True)
    res = scanner.scan(as_of_date=as_of, top_n=args.top)
    scanner.print_report(res)
    scanner.db.close()
