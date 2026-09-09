"""
Module: src/data/indices.py
Purpose: Ingests official NSE Sector & Benchmark indices and calculates Sector Rotation & Mansfield RS.
"""
import csv
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from src.core.config import ConfigManager, RulesConfig
from src.data.db import DuckDBManager
from src.data.http_client import NSEBSEHttpClient
from src.data.models import IndexQuote


class IndexIngestionEngine:
    """Manages index ingestion, sector momentum rankings, and Mansfield Relative Strength."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        http_client: Optional[NSEBSEHttpClient] = None,
        config_manager: Optional[ConfigManager] = None,
    ) -> None:
        self.db = db_manager or DuckDBManager()
        self.http = http_client or NSEBSEHttpClient()
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.get_config()

    def parse_nse_index_csv(
        self, csv_text: str, fallback_date: Optional[date] = None, filter_tracked: bool = True
    ) -> List[IndexQuote]:
        """
        PSEUDOCODE:
        1. Parse CSV rows from raw NSE ind_close_all_*.csv text.
        2. If filter_tracked is True, ignore non-tracked bond/futures/derivative indices.
        3. Clean numeric fields (handling commas, hyphens, and whitespace).
        4. Convert date into standard python date.
        5. Return list of IndexQuote entities.
        """
        quotes: List[IndexQuote] = []
        if not csv_text:
            return quotes

        tracked = self.config.indices.tracked_set if filter_tracked else None
        reader = csv.DictReader(csv_text.splitlines())
        for row in reader:
            idx_name = row.get("Index Name", "").strip()
            if not idx_name:
                continue
            if tracked and idx_name not in tracked:
                continue

            dt_str = row.get("Index Date", "").strip()
            dt = fallback_date
            if dt_str:
                for fmt in ("%d-%m-%Y", "%d-%b-%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(dt_str, fmt).date()
                        break
                    except ValueError:
                        pass

            if not dt:
                continue

            def _clean_float(val: Optional[str]) -> float:
                if not val or val.strip() in ("-", "", "null", "N/A"):
                    return 0.0
                return float(val.replace(",", "").strip())

            try:
                quotes.append(IndexQuote(
                    index_name=idx_name, trade_date=dt,
                    open_price=_clean_float(row.get("Open Index Value")),
                    high_price=_clean_float(row.get("High Index Value")),
                    low_price=_clean_float(row.get("Low Index Value")),
                    close_price=_clean_float(row.get("Closing Index Value")),
                    change_pct=_clean_float(row.get("Change(%)") or row.get("Points Change")),
                    pe_ratio=_clean_float(row.get("P/E")),
                    pb_ratio=_clean_float(row.get("P/B")),
                    div_yield=_clean_float(row.get("Div Yield")),
                ))
            except (ValueError, KeyError):
                continue

        return quotes

    def fetch_and_ingest_date(self, trade_date: date) -> int:
        """
        PSEUDOCODE:
        1. Fetch daily index CSV from NSE archive for trade_date.
        2. Parse CSV into IndexQuote models with tracked index filtering.
        3. Bulk upsert into DuckDB index_quotes table and return count.
        """
        url = f"https://archives.nseindia.com/content/indices/ind_close_all_{trade_date.strftime('%d%m%Y')}.csv"
        try:
            resp = self.http.session.get(url, timeout=self.http.timeout)
            if resp.status_code == 200 and resp.text:
                quotes = self.parse_nse_index_csv(resp.text, fallback_date=trade_date)
                return self.db.upsert_index_quotes(quotes)
        except Exception:
            pass
        return 0

    def get_sector_rankings(self, as_of_date: Optional[date] = None) -> List[Tuple[str, float, float]]:
        """
        PSEUDOCODE:
        1. Query index_quotes dynamically for all configured sector indices.
        2. Calculate 3-month (63d) and 6-month (126d) percentage returns.
        3. Return sorted list of (sector_name, return_3m, return_6m).
        """
        sectors = self.config.indices.sectors
        if not sectors:
            return []

        placeholders = ",".join(["?" for _ in sectors])
        query = f"""
            WITH sector_history AS (
                SELECT index_name, trade_date, close_price,
                       LAG(close_price, 63) OVER (PARTITION BY index_name ORDER BY trade_date) as close_63d,
                       LAG(close_price, 126) OVER (PARTITION BY index_name ORDER BY trade_date) as close_126d
                FROM index_quotes
                WHERE index_name IN ({placeholders})
            ),
            latest AS (
                SELECT index_name, trade_date, close_price, close_63d, close_126d,
                       ROW_NUMBER() OVER (PARTITION BY index_name ORDER BY trade_date DESC) as rn
                FROM sector_history
            )
            SELECT index_name,
                   ROUND(CASE WHEN close_63d > 0 THEN ((close_price - close_63d) / close_63d) * 100.0 ELSE 0.0 END, 2) as ret_3m,
                   ROUND(CASE WHEN close_126d > 0 THEN ((close_price - close_126d) / close_126d) * 100.0 ELSE 0.0 END, 2) as ret_6m
            FROM latest
            WHERE rn = 1
            ORDER BY ret_3m DESC;
        """
        rows = self.db.conn.execute(query, sectors).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def calculate_mansfield_rs(self, isin: str, benchmark: str = "Nifty 50") -> float:
        """
        PSEUDOCODE:
        1. Join stock eod_quotes with benchmark index_quotes on trade_date.
        2. Compute daily Relative Strength ratio RS = stock_close / index_close.
        3. Compute 252-day SMA of RS.
        4. Calculate Mansfield RS = ((RS_latest / SMA(RS, 252)) - 1) * 100.
        """
        query = """
            WITH joined AS (
                SELECT q.trade_date, (q.close_price / NULLIF(i.close_price, 0)) as rs
                FROM eod_quotes q
                JOIN index_quotes i ON q.trade_date = i.trade_date
                WHERE q.isin = ? AND i.index_name = ?
                ORDER BY q.trade_date ASC
            ),
            rs_stats AS (
                SELECT trade_date, rs,
                       AVG(rs) OVER (ORDER BY trade_date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) as rs_sma_252
                FROM joined
            )
            SELECT ROUND(((rs / NULLIF(rs_sma_252, 0)) - 1.0) * 100.0, 2)
            FROM rs_stats
            ORDER BY trade_date DESC
            LIMIT 1;
        """
        row = self.db.conn.execute(query, (isin, benchmark)).fetchone()
        return row[0] if row and row[0] is not None else 0.0

    def get_missing_trading_days(self, start_date: date, end_date: date) -> List[date]:
        """
        PSEUDOCODE:
        1. Query distinct trade_date from index_quotes table.
        2. Generate all weekdays between start_date and end_date.
        3. Return missing dates.
        """
        existing = {
            r[0] for r in self.db.conn.execute("SELECT DISTINCT trade_date FROM index_quotes;").fetchall()
        }
        missing: List[date] = []
        curr = start_date
        while curr <= end_date:
            if curr.weekday() < 5 and curr not in existing:
                missing.append(curr)
            curr += timedelta(days=1)
        return missing

    def _fetch_date_quotes(self, trade_date: date) -> List[IndexQuote]:
        """Fetch and parse single date index quotes."""
        url = f"https://archives.nseindia.com/content/indices/ind_close_all_{trade_date.strftime('%d%m%Y')}.csv"
        try:
            resp = self.http.session.get(url, timeout=self.http.timeout)
            if resp.status_code == 200 and resp.text:
                return self.parse_nse_index_csv(resp.text, fallback_date=trade_date)
        except Exception:
            pass
        return []

    def ingest_range(
        self, start_date: date, end_date: Optional[date] = None, max_workers: int = 25
    ) -> Tuple[int, int]:
        """
        PSEUDOCODE:
        1. Resolve missing dates between start_date and end_date.
        2. Concurrently download index CSVs using ThreadPoolExecutor.
        3. Batch insert quotes into DuckDB with progress bar.
        4. Return tuple of (stored_quotes, completed_days).
        """
        import concurrent.futures
        from tqdm import tqdm

        target_end = end_date or date.today()
        missing_days = self.get_missing_trading_days(start_date, target_end)
        if not missing_days:
            return 0, 0

        all_quotes: List[IndexQuote] = []
        completed_days = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_date = {executor.submit(self._fetch_date_quotes, d): d for d in missing_days}
            with tqdm(total=len(missing_days), desc="Index Historical Ingestion", unit="day") as pbar:
                for future in concurrent.futures.as_completed(future_to_date):
                    quotes = future.result()
                    if quotes:
                        all_quotes.extend(quotes)
                        completed_days += 1
                        if len(all_quotes) >= 5000:
                            self.db.upsert_index_quotes(all_quotes)
                            all_quotes.clear()
                    pbar.update(1)

        if all_quotes:
            self.db.upsert_index_quotes(all_quotes)

        return len(all_quotes), completed_days


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlphaCraft Historical Index Ingestion Engine")
    parser.add_argument("--start", type=str, default="2020-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--workers", type=int, default=25, help="Concurrent worker threads")
    args = parser.parse_args()

    s_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    eng = IndexIngestionEngine()
    print(f"Starting Historical Index Data Bootstrap: {s_date} to {date.today()} ({args.workers} workers)...")
    _, days = eng.ingest_range(s_date, date.today(), max_workers=args.workers)
    print(f"\nHistorical Index Ingestion Complete: {days} trading days stored in DuckDB.")
    eng.db.close()

