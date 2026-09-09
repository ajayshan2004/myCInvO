"""
Module: src/data/db.py
Purpose: DuckDB local analytical database manager for securities and quotes.
"""
from pathlib import Path
from typing import List, Optional
import duckdb
from src.data.models import Security, EODQuote, IndexQuote, QuarterlyFinancial, ShareholdingPattern, ListingStatus


class DuckDBManager:
    """Manages DuckDB analytical database connection and schemas."""

    def __init__(self, db_path: Optional[str] = None, read_only: bool = False) -> None:
        """
        PSEUDOCODE:
        1. Set db_path to default '.data/alphacraft.duckdb' if None.
        2. Open DuckDB connection with read_only flag.
        3. If not read_only, initialize schema tables.
        """
        if db_path is None:
            data_dir = Path(".data")
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(data_dir / "alphacraft.duckdb")
        else:
            self.db_path = db_path
        self.conn = duckdb.connect(self.db_path, read_only=read_only)
        if not read_only:
            self._init_schema()

    def _init_schema(self) -> None:
        """
        PSEUDOCODE:
        1. Create 'securities' master table (PK: isin).
        2. Create 'eod_quotes' time-series table (PK: isin, exchange, trade_date).
        3. Create 'index_quotes' table (PK: index_name, trade_date).
        4. Create 'quarterly_financials' table (PK: isin, period_end).
        5. Create 'shareholding_patterns' table (PK: isin, period_end).
        6. Create 'system_metadata' table (PK: key) for delta hashing and state.
        """
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS securities (
                isin VARCHAR PRIMARY KEY, company_name VARCHAR NOT NULL,
                listing_status VARCHAR NOT NULL, instrument_type VARCHAR NOT NULL DEFAULT 'EQUITY',
                nse_symbol VARCHAR, bse_code VARCHAR, bse_scrip_id VARCHAR, industry VARCHAR,
                face_value DOUBLE DEFAULT 10.0, is_active BOOLEAN DEFAULT TRUE
            );
            CREATE TABLE IF NOT EXISTS eod_quotes (
                isin VARCHAR NOT NULL, symbol VARCHAR NOT NULL,
                exchange VARCHAR NOT NULL, trade_date DATE NOT NULL,
                open_price DOUBLE NOT NULL, high_price DOUBLE NOT NULL,
                low_price DOUBLE NOT NULL, close_price DOUBLE NOT NULL,
                prev_close DOUBLE NOT NULL, total_volume BIGINT NOT NULL,
                deliverable_volume BIGINT DEFAULT 0, delivery_pct DOUBLE DEFAULT 0.0,
                PRIMARY KEY (isin, trade_date)
            );
            CREATE TABLE IF NOT EXISTS index_quotes (
                index_name VARCHAR NOT NULL, trade_date DATE NOT NULL,
                open_price DOUBLE NOT NULL, high_price DOUBLE NOT NULL,
                low_price DOUBLE NOT NULL, close_price DOUBLE NOT NULL,
                change_pct DOUBLE DEFAULT 0.0, pe_ratio DOUBLE DEFAULT 0.0,
                pb_ratio DOUBLE DEFAULT 0.0, div_yield DOUBLE DEFAULT 0.0,
                PRIMARY KEY (index_name, trade_date)
            );
            CREATE TABLE IF NOT EXISTS quarterly_financials (
                isin VARCHAR NOT NULL, symbol VARCHAR NOT NULL, period_end DATE NOT NULL,
                sales_cr DOUBLE NOT NULL, operating_profit_cr DOUBLE NOT NULL,
                opm_pct DOUBLE NOT NULL, net_profit_cr DOUBLE NOT NULL,
                pat_growth_yoy DOUBLE DEFAULT 0.0, sales_growth_yoy DOUBLE DEFAULT 0.0,
                eps DOUBLE DEFAULT 0.0,
                PRIMARY KEY (isin, period_end)
            );
            CREATE TABLE IF NOT EXISTS shareholding_patterns (
                isin VARCHAR NOT NULL, symbol VARCHAR NOT NULL, period_end DATE NOT NULL,
                promoter_pct DOUBLE NOT NULL, fii_pct DOUBLE NOT NULL, dii_pct DOUBLE NOT NULL,
                public_retail_pct DOUBLE NOT NULL, pledged_pct DOUBLE DEFAULT 0.0,
                retail_shareholders_count BIGINT DEFAULT 0,
                PRIMARY KEY (isin, period_end)
            );
            CREATE TABLE IF NOT EXISTS system_metadata (
                key VARCHAR PRIMARY KEY,
                value VARCHAR NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)


    def set_metadata(self, key: str, value: str) -> None:
        """
        PSEUDOCODE:
        1. Execute INSERT OR REPLACE on system_metadata table with key and value.
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO system_metadata (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP);",
            (key, value)
        )

    def get_metadata(self, key: str) -> Optional[str]:
        """
        PSEUDOCODE:
        1. Query value from system_metadata for the given key.
        2. Return string value if found, else None.
        """
        row = self.conn.execute("SELECT value FROM system_metadata WHERE key = ? LIMIT 1;", (key,)).fetchone()
        return row[0] if row else None

    def upsert_securities(self, securities: List[Security]) -> int:
        """
        PSEUDOCODE:
        1. If pyarrow is available, convert list to PyArrow Table and bulk INSERT OR REPLACE.
        2. Else fallback to executemany.
        3. Return total inserted record count.
        """
        if not securities:
            return 0
        try:
            import pyarrow as pa
            tbl = pa.table({
                "isin": [s.isin for s in securities],
                "company_name": [s.company_name for s in securities],
                "listing_status": [s.listing_status.value for s in securities],
                "instrument_type": [s.instrument_type.value for s in securities],
                "nse_symbol": [s.nse_symbol for s in securities],
                "bse_code": [s.bse_code for s in securities],
                "bse_scrip_id": [s.bse_scrip_id for s in securities],
                "industry": [s.industry for s in securities],
                "face_value": [s.face_value for s in securities],
                "is_active": [s.is_active for s in securities],
            })
            self.conn.execute("INSERT OR REPLACE INTO securities SELECT * FROM tbl;")
        except ImportError:
            records = [
                (s.isin, s.company_name, s.listing_status.value, s.instrument_type.value,
                 s.nse_symbol, s.bse_code, s.bse_scrip_id, s.industry, s.face_value, s.is_active)
                for s in securities
            ]
            self.conn.executemany("INSERT OR REPLACE INTO securities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", records)
        return len(securities)

    def upsert_eod_quotes(self, quotes: List[EODQuote]) -> int:
        """
        PSEUDOCODE:
        1. If pyarrow is available, convert list to PyArrow Table and bulk INSERT OR REPLACE.
        2. Else fallback to executemany.
        3. Return count of processed quotes.
        """
        if not quotes:
            return 0
        try:
            import pyarrow as pa
            tbl = pa.table({
                "isin": [q.isin for q in quotes],
                "symbol": [q.symbol for q in quotes],
                "exchange": [q.exchange.value for q in quotes],
                "trade_date": [q.trade_date for q in quotes],
                "open_price": [q.open_price for q in quotes],
                "high_price": [q.high_price for q in quotes],
                "low_price": [q.low_price for q in quotes],
                "close_price": [q.close_price for q in quotes],
                "prev_close": [q.prev_close for q in quotes],
                "total_volume": [q.total_volume for q in quotes],
                "deliverable_volume": [q.deliverable_volume for q in quotes],
                "delivery_pct": [q.delivery_pct for q in quotes],
            })
            self.conn.execute("INSERT OR REPLACE INTO eod_quotes SELECT * FROM tbl;")
        except ImportError:
            records = [
                (q.isin, q.symbol, q.exchange.value, q.trade_date, q.open_price,
                  q.high_price, q.low_price, q.close_price, q.prev_close,
                  q.total_volume, q.deliverable_volume, q.delivery_pct)
                for q in quotes
            ]
            self.conn.executemany("INSERT OR REPLACE INTO eod_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", records)
        return len(quotes)

    def upsert_index_quotes(self, quotes: List[IndexQuote]) -> int:
        """
        PSEUDOCODE:
        1. If pyarrow is available, convert list to PyArrow Table and bulk INSERT OR REPLACE.
        2. Else fallback to executemany.
        3. Return count of processed index quotes.
        """
        if not quotes:
            return 0
        try:
            import pyarrow as pa
            tbl = pa.table({
                "index_name": [q.index_name for q in quotes],
                "trade_date": [q.trade_date for q in quotes],
                "open_price": [q.open_price for q in quotes],
                "high_price": [q.high_price for q in quotes],
                "low_price": [q.low_price for q in quotes],
                "close_price": [q.close_price for q in quotes],
                "change_pct": [q.change_pct for q in quotes],
                "pe_ratio": [q.pe_ratio for q in quotes],
                "pb_ratio": [q.pb_ratio for q in quotes],
                "div_yield": [q.div_yield for q in quotes],
            })
            self.conn.execute("INSERT OR REPLACE INTO index_quotes SELECT * FROM tbl;")
        except ImportError:
            records = [
                (q.index_name, q.trade_date, q.open_price, q.high_price, q.low_price,
                 q.close_price, q.change_pct, q.pe_ratio, q.pb_ratio, q.div_yield)
                for q in quotes
            ]
            self.conn.executemany("INSERT OR REPLACE INTO index_quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", records)
        return len(quotes)

    def upsert_quarterly_financials(self, records: List[QuarterlyFinancial]) -> int:
        """
        PSEUDOCODE:
        1. Bulk INSERT OR REPLACE into quarterly_financials using PyArrow or executemany.
        2. Return processed records count.
        """
        if not records:
            return 0
        try:
            import pyarrow as pa
            tbl = pa.table({
                "isin": [r.isin for r in records],
                "symbol": [r.symbol for r in records],
                "period_end": [r.period_end for r in records],
                "sales_cr": [r.sales_cr for r in records],
                "operating_profit_cr": [r.operating_profit_cr for r in records],
                "opm_pct": [r.opm_pct for r in records],
                "net_profit_cr": [r.net_profit_cr for r in records],
                "pat_growth_yoy": [r.pat_growth_yoy for r in records],
                "sales_growth_yoy": [r.sales_growth_yoy for r in records],
                "eps": [r.eps for r in records],
            })
            self.conn.execute("INSERT OR REPLACE INTO quarterly_financials SELECT * FROM tbl;")
        except ImportError:
            tuples = [
                (r.isin, r.symbol, r.period_end, r.sales_cr, r.operating_profit_cr,
                 r.opm_pct, r.net_profit_cr, r.pat_growth_yoy, r.sales_growth_yoy, r.eps)
                for r in records
            ]
            self.conn.executemany("INSERT OR REPLACE INTO quarterly_financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);", tuples)
        return len(records)

    def upsert_shareholding_patterns(self, records: List[ShareholdingPattern]) -> int:
        """
        PSEUDOCODE:
        1. Bulk INSERT OR REPLACE into shareholding_patterns using PyArrow or executemany.
        2. Return processed records count.
        """
        if not records:
            return 0
        try:
            import pyarrow as pa
            tbl = pa.table({
                "isin": [r.isin for r in records],
                "symbol": [r.symbol for r in records],
                "period_end": [r.period_end for r in records],
                "promoter_pct": [r.promoter_pct for r in records],
                "fii_pct": [r.fii_pct for r in records],
                "dii_pct": [r.dii_pct for r in records],
                "public_retail_pct": [r.public_retail_pct for r in records],
                "pledged_pct": [r.pledged_pct for r in records],
                "retail_shareholders_count": [r.retail_shareholders_count for r in records],
            })
            self.conn.execute("INSERT OR REPLACE INTO shareholding_patterns SELECT * FROM tbl;")
        except ImportError:
            tuples = [
                (r.isin, r.symbol, r.period_end, r.promoter_pct, r.fii_pct, r.dii_pct,
                 r.public_retail_pct, r.pledged_pct, r.retail_shareholders_count)
                for r in records
            ]
            self.conn.executemany("INSERT OR REPLACE INTO shareholding_patterns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);", tuples)
        return len(records)

    def get_security(self, identifier: str) -> Optional[Security]:
        """
        PSEUDOCODE:
        1. Query securities where isin = identifier OR nse_symbol = identifier OR bse_code = identifier.
        2. Return mapped Security domain entity with instrument_type, else None.
        """
        res = self.conn.execute("""
            SELECT isin, company_name, listing_status, instrument_type,
                   nse_symbol, bse_code, bse_scrip_id, industry, face_value, is_active
            FROM securities WHERE isin = ? OR nse_symbol = ? OR bse_code = ? LIMIT 1;
        """, (identifier, identifier, identifier)).fetchone()
        if not res:
            return None
        from src.data.models import InstrumentType
        return Security(
            isin=res[0], company_name=res[1], listing_status=ListingStatus(res[2]),
            instrument_type=InstrumentType(res[3]),
            nse_symbol=res[4], bse_code=res[5], bse_scrip_id=res[6],
            industry=res[7], face_value=res[8], is_active=res[9]
        )


    def close(self) -> None:
        """Close database connection."""
        self.conn.close()



