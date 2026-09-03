"""
Module: src/data/historical.py
Purpose: High-throughput concurrent historical market data ingestion engine for 2020+.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple
from src.data.bhavcopy import BhavcopyService
from src.data.db import DuckDBManager
from src.data.http_client import NSEBSEHttpClient
from src.data.models import EODQuote, Exchange


class HistoricalIngestionEngine:
    """Orchestrates multi-threaded historical Bhavcopy ingestion across date ranges."""

    def __init__(self, db_manager: Optional[DuckDBManager] = None, http_client: Optional[NSEBSEHttpClient] = None) -> None:
        self.db = db_manager or DuckDBManager()
        self.http = http_client or NSEBSEHttpClient()
        self.bhavcopy = BhavcopyService(self.db)
        # Pre-cache symbol-to-ISIN lookups from DuckDB
        nse_rows = self.db.conn.execute("SELECT nse_symbol, isin FROM securities WHERE nse_symbol IS NOT NULL;").fetchall()
        self.nse_lookup = {r[0]: r[1] for r in nse_rows}
        bse_rows = self.db.conn.execute("SELECT bse_code, isin FROM securities WHERE bse_code IS NOT NULL;").fetchall()
        self.bse_lookup = {r[0]: r[1] for r in bse_rows}

    def get_missing_trading_days(self, start_date: date, end_date: date) -> List[date]:
        """
        PSEUDOCODE:
        1. Query distinct trade_date already stored in eod_quotes table.
        2. Generate all weekday dates (Mon-Fri) between start_date and end_date inclusive.
        3. Return list of missing weekdays that are not yet stored in DuckDB.
        """
        existing = {
            r[0] for r in self.db.conn.execute("SELECT DISTINCT trade_date FROM eod_quotes;").fetchall()
        }
        missing_days: List[date] = []
        curr = start_date
        while curr <= end_date:
            if curr.weekday() < 5 and curr not in existing:
                missing_days.append(curr)
            curr += timedelta(days=1)
        return missing_days

    def _fetch_and_parse_date(self, trade_date: date) -> Tuple[date, List[EODQuote], List[EODQuote]]:
        """
        PSEUDOCODE:
        1. Fetch NSE Bhavcopy and parse into EODQuote models using pre-cached lookup.
        2. Fetch BSE Bhavcopy and parse into EODQuote models using pre-cached lookup.
        3. Return tuple of trade_date, nse_quotes, and bse_quotes.
        """
        nse_quotes: List[EODQuote] = []
        bse_quotes: List[EODQuote] = []

        nse_csv = self.http.fetch_nse_bhavcopy(trade_date)
        if nse_csv:
            nse_quotes = self.bhavcopy.parse_nse_bhavcopy(nse_csv, trade_date, self.nse_lookup)

        bse_csv = self.http.fetch_bse_bhavcopy(trade_date)
        if bse_csv:
            bse_quotes = self.bhavcopy.parse_bse_bhavcopy(bse_csv, trade_date, self.bse_lookup)

        return trade_date, nse_quotes, bse_quotes


    def ingest_range(self, start_date: date, end_date: date, max_workers: int = 20) -> Dict[str, int]:
        """
        PSEUDOCODE:
        1. Identify missing weekdays in [start_date, end_date].
        2. Download and parse Bhavcopies concurrently across max_workers threads.
        3. Deduplicate (NSE priority + BSE fallback) and batch-upsert into DuckDB.
        4. Return summary dictionary of total days and quotes ingested.
        """
        missing_days = self.get_missing_trading_days(start_date, end_date)
        if not missing_days:
            return {"total_days": 0, "total_quotes": 0}

        total_quotes = 0
        completed_days = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._fetch_and_parse_date, dt) for dt in missing_days]
            for future in as_completed(futures):
                dt, nse_quotes, bse_quotes = future.result()
                if not nse_quotes and not bse_quotes:
                    continue  # Exchange holiday

                # Deduplicate: Only keep BSE quotes for ISINs not present in NSE quotes
                nse_isins = {q.isin for q in nse_quotes}
                deduped_bse_quotes = [q for q in bse_quotes if q.isin not in nse_isins]
                all_day_quotes = nse_quotes + deduped_bse_quotes

                inserted = self.db.upsert_eod_quotes(all_day_quotes)
                total_quotes += inserted
                completed_days += 1

        return {
            "total_days": completed_days,
            "total_quotes": total_quotes
        }

    def close(self) -> None:
        """Close database connection."""
        self.db.close()
