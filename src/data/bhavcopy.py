"""
Module: src/data/bhavcopy.py
Purpose: Bulk daily Bhavcopy ingestion with delta processing and observer events (NSE + BSE).
"""
import csv
import io
from datetime import date
from typing import Callable, Dict, List, Optional
from src.data.models import Exchange, EODQuote
from src.data.db import DuckDBManager


class BhavcopyService:
    """Service to parse bulk EOD market data, enforce delta caching, and notify observers."""

    def __init__(self, db_manager: DuckDBManager) -> None:
        self.db = db_manager
        self._listeners: List[Callable[[Exchange, date, int], None]] = []

    def register_listener(self, callback: Callable[[Exchange, date, int], None]) -> None:
        """PSEUDOCODE: Register callback invoked on new daily quote ingestion."""
        self._listeners.append(callback)

    def parse_nse_bhavcopy(
        self, csv_text: str, trade_date: date, isin_lookup: Optional[Dict[str, str]] = None
    ) -> List[EODQuote]:
        """
        PSEUDOCODE:
        1. Read NSE Bhavcopy CSV (sec_bhavdata_full format).
        2. Filter active equity series ('EQ', 'BE', 'SM', 'BZ').
        3. Extract prices, total volume, deliverable volume, and delivery %.
        4. Return list of valid EODQuote domain objects.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        quotes: List[EODQuote] = []
        for row in reader:
            clean = {k.strip().upper(): v.strip() for k, v in row.items() if k and v}
            series = clean.get("SERIES", "")
            if series not in {"EQ", "BE", "SM", "BZ"}:
                continue

            symbol = clean.get("SYMBOL", "")
            isin = clean.get("ISIN", "")
            if not isin or len(isin) != 12:
                isin = (isin_lookup or {}).get(symbol, "")
            if not isin or len(isin) != 12:
                continue

            try:
                op, hp = float(clean.get("OPEN_PRICE", clean.get("OPEN", 0.0))), float(clean.get("HIGH_PRICE", clean.get("HIGH", 0.0)))
                lp, cp = float(clean.get("LOW_PRICE", clean.get("LOW", 0.0))), float(clean.get("CLOSE_PRICE", clean.get("CLOSE", 0.0)))
                prev_c = float(clean.get("PREV_CLOSE", clean.get("PREVCLOSE", cp)))
                vol = int(clean.get("TTL_TRD_QNTY", clean.get("TOTTRDQTY", 0)))
                
                deliv_vol = int(clean.get("DELIV_QTY", clean.get("DELIVERY_QTY", 0))) if clean.get("DELIV_QTY", "").isdigit() else 0
                deliv_vol = min(deliv_vol, vol)
                deliv_pct = float(clean.get("DELIV_PER", clean.get("DELIVERY_PCT", (deliv_vol / vol * 100.0) if vol > 0 else 0.0)))
                
                hp, lp = max(op, hp, cp), min(op, lp, cp)
                quotes.append(EODQuote(
                    isin=isin, symbol=symbol, exchange=Exchange.NSE, trade_date=trade_date,
                    open_price=op, high_price=hp, low_price=lp, close_price=cp, prev_close=prev_c,
                    total_volume=vol, deliverable_volume=deliv_vol, delivery_pct=min(100.0, max(0.0, deliv_pct))
                ))
            except (ValueError, TypeError):
                continue
        return quotes

    def parse_bse_bhavcopy(
        self, csv_text: str, trade_date: date, code_to_isin: Optional[Dict[str, str]] = None
    ) -> List[EODQuote]:
        """
        PSEUDOCODE:
        1. Read BSE Bhavcopy CSV.
        2. Resolve ISIN from row or code_to_isin mapping.
        3. Extract OHLC and total volume.
        4. Return list of valid EODQuote domain objects.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        quotes: List[EODQuote] = []
        for row in reader:
            clean = {k.strip().upper(): v.strip() for k, v in row.items() if k and v}
            sc_code = clean.get("SC_CODE", clean.get("SCRIP_CD", ""))
            symbol = clean.get("SC_NAME", clean.get("SCRIP_NAME", sc_code))
            isin = clean.get("ISIN_CODE", clean.get("ISIN", (code_to_isin or {}).get(sc_code, "")))
            if not isin or len(isin) != 12:
                continue

            try:
                op, hp = float(clean.get("OPEN", 0.0)), float(clean.get("HIGH", 0.0))
                lp, cp = float(clean.get("LOW", 0.0)), float(clean.get("CLOSE", 0.0))
                prev_c = float(clean.get("PREVCLOSE", cp))
                vol = int(clean.get("NO_OF_SHRS", clean.get("VOLUME", 0)))

                hp, lp = max(op, hp, cp), min(op, lp, cp)
                quotes.append(EODQuote(
                    isin=isin, symbol=symbol, exchange=Exchange.BSE, trade_date=trade_date,
                    open_price=op, high_price=hp, low_price=lp, close_price=cp, prev_close=prev_c,
                    total_volume=vol, deliverable_volume=0, delivery_pct=0.0
                ))
            except (ValueError, TypeError):
                continue
        return quotes

    def ingest_bhavcopy(
        self, exchange: Exchange, trade_date: date, csv_text: str, id_lookup: Optional[Dict[str, str]] = None
    ) -> int:
        """
        PSEUDOCODE:
        1. Check delta key 'bhavcopy_{exchange}_{date}'. If exists, skip (return 0).
        2. Parse exchange CSV into EODQuote models.
        3. Batch upsert quotes to DuckDB.
        4. Record delta key in system_metadata.
        5. Notify registered downstream listeners.
        """
        delta_key = f"bhavcopy_{exchange.value}_{trade_date.isoformat()}"
        if self.db.get_metadata(delta_key):
            return 0  # Principle #2: Delta skip

        if exchange == Exchange.NSE:
            quotes = self.parse_nse_bhavcopy(csv_text, trade_date, id_lookup)
        else:
            quotes = self.parse_bse_bhavcopy(csv_text, trade_date, id_lookup)

        count = self.db.upsert_eod_quotes(quotes)
        self.db.set_metadata(delta_key, f"records:{count}")

        # Principle #3: Notify downstream modules
        for listener in self._listeners:
            listener(exchange, trade_date, count)

        return count
