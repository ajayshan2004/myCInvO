"""
Module: src/data/sync.py
Purpose: Market data synchronization engine enforcing delta-only downloads and updates.
"""
from datetime import date, timedelta
from typing import Dict, Optional
from src.data.models import Exchange
from src.data.db import DuckDBManager
from src.data.universe import UniverseService
from src.data.bhavcopy import BhavcopyService
from src.data.http_client import NSEBSEHttpClient


class MarketDataSync:
    """Orchestrates delta-only ingestion for master universe and daily Bhavcopy."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        http_client: Optional[NSEBSEHttpClient] = None
    ) -> None:
        self.db = db_manager or DuckDBManager()
        self.http = http_client or NSEBSEHttpClient()
        self.universe_svc = UniverseService(self.db)
        self.bhavcopy_svc = BhavcopyService(self.db)

    def sync_universe(self) -> int:
        """
        PSEUDOCODE:
        1. Fetch NSE and BSE live master CSVs.
        2. Pass to universe service (which evaluates delta MD5 hash).
        3. Return count of added/updated securities (0 if unchanged).
        """
        nse_csv = self.http.fetch_nse_master()
        bse_csv = self.http.fetch_bse_master()
        return self.universe_svc.sync_universe(nse_csv=nse_csv, bse_csv=bse_csv)

    def sync_bhavcopy_for_date(self, trade_date: date) -> Dict[str, int]:
        """
        PSEUDOCODE:
        1. Check delta guard for NSE and BSE on trade_date.
        2. If NSE missing, download NSE Bhavcopy and ingest.
        3. If BSE missing, download BSE Bhavcopy and ingest.
        4. Return dictionary of ingested record counts per exchange.
        """
        results = {"NSE": 0, "BSE": 0}
        nse_delta_key = f"bhavcopy_NSE_{trade_date.isoformat()}"
        bse_delta_key = f"bhavcopy_BSE_{trade_date.isoformat()}"

        # 1. Delta check and ingest NSE
        if not self.db.get_metadata(nse_delta_key):
            nse_csv = self.http.fetch_nse_bhavcopy(trade_date)
            if nse_csv:
                results["NSE"] = self.bhavcopy_svc.ingest_bhavcopy(Exchange.NSE, trade_date, nse_csv)

        # 2. Delta check and ingest BSE
        if not self.db.get_metadata(bse_delta_key):
            bse_csv = self.http.fetch_bse_bhavcopy(trade_date)
            if bse_csv:
                results["BSE"] = self.bhavcopy_svc.ingest_bhavcopy(Exchange.BSE, trade_date, bse_csv)

        return results


    def run_daily_sync(self, target_date: Optional[date] = None) -> Dict[str, any]:
        """
        PSEUDOCODE:
        1. Default target_date to today (or yesterday if Sunday/Monday morning).
        2. Execute delta universe sync.
        3. Execute delta Bhavcopy sync.
        4. Return combined execution summary.
        """
        sync_date = target_date or date.today()
        # Fallback to previous weekday if weekend
        if sync_date.weekday() == 6:  # Sunday -> Friday
            sync_date -= timedelta(days=2)
        elif sync_date.weekday() == 5:  # Saturday -> Friday
            sync_date -= timedelta(days=1)

        universe_updates = self.sync_universe()
        quote_updates = self.sync_bhavcopy_for_date(sync_date)

        return {
            "trade_date": sync_date.isoformat(),
            "universe_updates": universe_updates,
            "quotes_ingested": quote_updates,
        }

    def close(self) -> None:
        """PSEUDOCODE: Close underlying database connection."""
        self.db.close()


if __name__ == "__main__":
    sync_engine = MarketDataSync()
    try:
        summary = sync_engine.run_daily_sync()
        print(f"Sync Complete: {summary}")
    finally:
        sync_engine.close()

