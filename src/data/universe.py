import csv
import hashlib
import io
from typing import Callable, Dict, List, Optional
from src.data.models import ListingStatus, Security
from src.data.db import DuckDBManager


class UniverseService:
    """Service to discover, parse, and synchronize master stock universe across NSE and BSE."""

    def __init__(self, db_manager: DuckDBManager) -> None:
        self.db = db_manager
        self._listeners: List[Callable[[int], None]] = []

    def register_listener(self, callback: Callable[[int], None]) -> None:
        """PSEUDOCODE: Register downstream callback triggered on universe changes."""
        self._listeners.append(callback)

    def parse_nse_master(self, csv_text: str) -> Dict[str, dict]:
        """
        PSEUDOCODE:
        1. Read CSV text using DictReader.
        2. Filter active equity series ('EQ', 'BE', 'SM', 'BZ').
        3. Extract ISIN, symbol, company name, and face value.
        4. Return dictionary keyed by 12-char ISIN.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        records: Dict[str, dict] = {}
        for row in reader:
            clean = {k.strip().upper(): v.strip() for k, v in row.items() if k and v}
            isin = clean.get("ISIN NUMBER", clean.get("ISIN", ""))
            series = clean.get("SERIES", "")
            if len(isin) == 12 and series in {"EQ", "BE", "SM", "BZ"}:
                try:
                    face_val = float(clean.get("FACE VALUE", "10"))
                except ValueError:
                    face_val = 10.0
                records[isin] = {
                    "isin": isin, "nse_symbol": clean.get("SYMBOL", ""),
                    "company_name": clean.get("NAME OF COMPANY", ""), "face_value": face_val
                }
        return records

    def parse_bse_master(self, csv_text: str) -> Dict[str, dict]:
        """
        PSEUDOCODE:
        1. Read CSV text using DictReader.
        2. Extract BSE Scrip code, Scrip ID, ISIN, and Industry.
        3. Return dictionary keyed by 12-char ISIN.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        records: Dict[str, dict] = {}
        for row in reader:
            clean = {k.strip().upper(): v.strip() for k, v in row.items() if k and v}
            isin = clean.get("ISIN NO", clean.get("ISIN_CODE", clean.get("ISIN", "")))
            bse_code = clean.get("SECURITY CODE", clean.get("SCRIP_CD", ""))
            if len(isin) == 12 and bse_code:
                records[isin] = {
                    "isin": isin, "bse_code": bse_code,
                    "bse_scrip_id": clean.get("SECURITY ID", clean.get("SCRIP_ID", "")),
                    "company_name": clean.get("SECURITY NAME", clean.get("SCRIP_NAME", "")),
                    "industry": clean.get("INDUSTRY", None)
                }
        return records

    def merge_masters(self, nse_data: Dict[str, dict], bse_data: Dict[str, dict]) -> List[Security]:
        """
        PSEUDOCODE:
        1. Create union of all unique ISIN keys.
        2. If present in both -> classify as DUAL_LISTED.
        3. If present only in NSE -> classify as NSE_ONLY.
        4. If present only in BSE -> classify as BSE_ONLY.
        5. Return list of unified Security domain objects.
        """
        all_isins = set(nse_data.keys()).union(set(bse_data.keys()))
        securities: List[Security] = []
        for isin in all_isins:
            nse, bse = nse_data.get(isin), bse_data.get(isin)
            if nse and bse:
                status = ListingStatus.DUAL_LISTED
                name, nse_sym = nse["company_name"] or bse["company_name"], nse["nse_symbol"]
                bse_cd, bse_id, ind, fv = bse["bse_code"], bse["bse_scrip_id"], bse["industry"], nse["face_value"]
            elif nse:
                status = ListingStatus.NSE_ONLY
                name, nse_sym, bse_cd, bse_id, ind, fv = nse["company_name"], nse["nse_symbol"], None, None, None, nse["face_value"]
            else:
                status = ListingStatus.BSE_ONLY
                name, nse_sym, bse_cd, bse_id, ind, fv = bse["company_name"], None, bse["bse_code"], bse["bse_scrip_id"], bse["industry"], 10.0
            securities.append(Security(
                isin=isin, company_name=name, listing_status=status,
                nse_symbol=nse_sym, bse_code=bse_cd, bse_scrip_id=bse_id,
                industry=ind, face_value=fv
            ))
        return securities

    def sync_universe(self, nse_csv: Optional[str] = None, bse_csv: Optional[str] = None) -> int:
        """
        PSEUDOCODE:
        1. Compute MD5 checksum of raw input CSV contents.
        2. Compare checksum with stored metadata; if unchanged, return 0 (Delta skip).
        3. Otherwise, parse available CSVs and merge into Security records.
        4. Batch upsert into DuckDB and update delta checksum.
        5. Notify all registered downstream listeners with updated count.
        """
        raw_payload = f"{nse_csv or ''}::{bse_csv or ''}"
        current_hash = hashlib.md5(raw_payload.encode("utf-8")).hexdigest()
        last_hash = self.db.get_metadata("universe_master_hash")

        # Principle #2: If nothing changed, skip execution
        if last_hash == current_hash:
            return 0

        nse_data = self.parse_nse_master(nse_csv) if nse_csv else {}
        bse_data = self.parse_bse_master(bse_csv) if bse_csv else {}
        merged = self.merge_masters(nse_data, bse_data)

        count = self.db.upsert_securities(merged)
        self.db.set_metadata("universe_master_hash", current_hash)

        # Principle #3: Notify downstream modules of data change
        for listener in self._listeners:
            listener(count)

        return count


