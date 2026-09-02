"""
Module: src/data/db.py
Purpose: DuckDB local analytical database manager for securities and quotes.
"""
from pathlib import Path
from typing import List, Optional
import duckdb
from src.data.models import Security, EODQuote, ListingStatus


class DuckDBManager:
    """Manages DuckDB analytical database connection and schemas."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        """PSEUDOCODE: 1. Default to '.data/alphacraft.duckdb' if None. 2. Init connection and schema."""
        if db_path is None:
            data_dir = Path(".data")
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(data_dir / "alphacraft.duckdb")
        else:
            self.db_path = db_path
        self.conn = duckdb.connect(self.db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """PSEUDOCODE: Create 'securities' (PK: isin) and 'eod_quotes' (PK: isin, exchange, trade_date)."""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                isin VARCHAR PRIMARY KEY, company_name VARCHAR NOT NULL,
                listing_status VARCHAR NOT NULL, nse_symbol VARCHAR,
                bse_code VARCHAR, bse_scrip_id VARCHAR, industry VARCHAR,
                face_value DOUBLE DEFAULT 10.0, is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE IF NOT EXISTS eod_quotes (
                isin VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL, trade_date DATE NOT NULL,
                open_price DOUBLE NOT NULL, high_price DOUBLE NOT NULL,
                low_price DOUBLE NOT NULL, close_price DOUBLE NOT NULL,
                prev_close DOUBLE NOT NULL, total_volume BIGINT NOT NULL,
                deliverable_volume BIGINT DEFAULT 0, delivery_pct DOUBLE DEFAULT 0.0,
                PRIMARY KEY (isin, exchange, trade_date)
            );
        """)

    def upsert_securities(self, securities: List[Security]) -> int:
        """PSEUDOCODE: Batch insert or replace into securities table."""
        if not securities:
            return 0
        records = [
            (s.isin, s.company_name, s.listing_status.value, s.nse_symbol,
             s.bse_code, s.bse_scrip_id, s.industry, s.face_value, s.is_active)
            for s in securities
        ]
        self.conn.executemany("INSERT OR REPLACE INTO securities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", records)
        return len(records)

    def upsert_eod_quotes(self, quotes: List[EODQuote]) -> int:
        """PSEUDOCODE: Batch insert or replace into eod_quotes table."""
        if not quotes:
            return 0
        records = [
            (q.isin, q.symbol, q.exchange.value, q.trade_date, q.open_price,
             q.high_price, q.low_price, q.close_price, q.prev_close,
             q.total_volume, q.deliverable_volume, q.delivery_pct)
            for q in quotes
        ]
        self.conn.executemany("INSERT OR REPLACE INTO eod_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", records)
        return len(records)

    def get_security(self, identifier: str) -> Optional[Security]:
        """PSEUDOCODE: Look up security by ISIN, NSE symbol, or BSE code."""
        res = self.conn.execute("""
            SELECT isin, company_name, listing_status, nse_symbol,
                   bse_code, bse_scrip_id, industry, face_value, is_active
            FROM securities WHERE isin = ? OR nse_symbol = ? OR bse_code = ? LIMIT 1;
        """, (identifier, identifier, identifier)).fetchone()
        if not res:
            return None
        return Security(
            isin=res[0], company_name=res[1], listing_status=ListingStatus(res[2]),
            nse_symbol=res[3], bse_code=res[4], bse_scrip_id=res[5],
            industry=res[6], face_value=res[7], is_active=res[8]
        )

    def close(self) -> None:
        """Close connection."""
        self.conn.close()

