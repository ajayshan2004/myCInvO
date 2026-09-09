"""
Script: scripts/benchmark_pipeline.py
Purpose: Executes the entire AlphaCraft end-to-end data lake ingestion pipeline with precise microsecond/second timing.
"""
import time
from datetime import date, datetime, timedelta
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.universe import UniverseService
from src.data.historical import HistoricalIngestionEngine
from src.data.indices import IndexIngestionEngine
from src.data.fundamentals import FundamentalsEngine
from src.data.http_client import NSEBSEHttpClient
from src.radar.scanner import RadarScanner


def run_full_pipeline_with_timing() -> None:
    print("\n" + "=" * 95)
    print("  ALPHACRAFT END-TO-END DATA LAKE INGESTION & RADAR PIPELINE BENCHMARK")
    print("=" * 95)
    total_start = time.perf_counter()
    timings = {}

    # Stage 1: Database Initialization
    s1_start = time.perf_counter()
    db = DuckDBManager()
    timings["db_init"] = time.perf_counter() - s1_start
    print(f"  [Stage 1] Database Initialized: .data/alphacraft.duckdb ({timings['db_init']*1000:.1f} ms)")

    # Stage 2: Universe Master Synchronization (NSE + BSE)
    s2_start = time.perf_counter()
    http = NSEBSEHttpClient()
    univ_svc = UniverseService(db)
    nse_master = http.fetch_nse_master()
    bse_master = http.fetch_bse_master()
    sec_count = univ_svc.sync_universe(nse_csv=nse_master, bse_csv=bse_master)
    timings["universe_sync"] = time.perf_counter() - s2_start
    print(f"  [Stage 2] Universe Master Synced: {sec_count:,} active securities ({timings['universe_sync']:.2f}s)")

    # Stage 3: Index & Sector Ingestion
    s3_start = time.perf_counter()
    index_engine = IndexIngestionEngine(db, http_client=http)
    idx_quotes_count = 0
    curr_date = date(2026, 9, 2)
    for i in range(120):
        t_date = curr_date - timedelta(days=i)
        if t_date.weekday() < 5:
            count = index_engine.fetch_and_ingest_date(t_date)
            idx_quotes_count += count
    timings["index_ingestion"] = time.perf_counter() - s3_start
    print(f"  [Stage 3] Sector & Benchmark Indices Ingested: {idx_quotes_count:,} index quotes ({timings['index_ingestion']:.2f}s)")

    # Stage 4: Historical EOD Bhavcopy & Delivery Ingestion
    s4_start = time.perf_counter()
    hist_engine = HistoricalIngestionEngine(db, http_client=http)
    start_trade_date = date(2026, 1, 1)
    end_trade_date = date(2026, 9, 2)
    hist_stats = hist_engine.ingest_range(start_trade_date, end_trade_date, max_workers=25, show_progress=False)
    timings["historical_quotes"] = time.perf_counter() - s4_start
    total_quotes_in_db = db.conn.execute("SELECT count(*) FROM eod_quotes;").fetchone()[0]
    print(f"  [Stage 4] EOD Quotes & Delivery Volume Ingested: {total_quotes_in_db:,} quotes across {hist_stats['total_days']} days ({timings['historical_quotes']:.2f}s)")

    # Stage 5: Quarterly Financials & Shareholding Ingestion
    s5_start = time.perf_counter()
    fund_engine = FundamentalsEngine(db)
    fund_count = fund_engine.populate_universe_fundamentals(num_quarters=8)
    sur_set = fund_engine.sync_surveillance_list(http_client=http)
    timings["fundamentals"] = time.perf_counter() - s5_start
    print(f"  [Stage 5] Fundamentals & Smart Money Ingested: {fund_count:,} quarterly financials & {fund_count:,} shareholding patterns ({timings['fundamentals']:.2f}s)")

    # Stage 6: Live Institutional Opportunity Radar Scan
    s6_start = time.perf_counter()
    scanner = RadarScanner(db_manager=db, read_only=True)
    scan_results = scanner.scan(as_of_date=date(2026, 9, 2), top_n=5)
    timings["radar_scan"] = time.perf_counter() - s6_start
    print(f"  [Stage 6] Institutional Multi-Lens Radar Scan Completed ({timings['radar_scan']:.2f}s)")

    total_time = time.perf_counter() - total_start

    # Render Scan Report
    scanner.print_report(scan_results)

    # Print Timing Summary Table
    print("=" * 95)
    print("  INGESTION & PIPELINE EXECUTION TIME BREAKDOWN")
    print("=" * 95)
    print(f"  {'Pipeline Stage':<45} {'Duration':<15} {'Percentage'}")
    print("-" * 95)
    for stage, dur in timings.items():
        pct = (dur / total_time) * 100.0
        dur_str = f"{dur*1000:.1f} ms" if dur < 1.0 else f"{dur:.2f} s"
        print(f"  {stage:<45} {dur_str:<15} {pct:>6.1f}%")
    print("-" * 95)
    print(f"  {'TOTAL PIPELINE DURATION':<45} {total_time:.2f} s       100.0%")
    print("=" * 95 + "\n")

    # Table Row Counts Summary
    counts = {
        tbl: db.conn.execute(f"SELECT count(*) FROM {tbl};").fetchone()[0]
        for tbl in ["securities", "eod_quotes", "index_quotes", "quarterly_financials", "shareholding_patterns", "system_metadata"]
    }
    print(f"  Final DuckDB Table Row Counts: {counts}\n")
    db.close()


if __name__ == "__main__":
    run_full_pipeline_with_timing()
