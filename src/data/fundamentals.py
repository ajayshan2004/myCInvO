"""
Module: src/data/fundamentals.py
Purpose: Analyzes CANSLIM quarterly earnings acceleration, Smart Money institutional trends, and Forensic Shield.
"""
from datetime import date
from typing import Any, Dict, List, Optional
from src.core.config import ConfigManager
from src.data.db import DuckDBManager
from src.data.models import QuarterlyFinancial, ShareholdingPattern


class FundamentalsEngine:
    """Evaluates CANSLIM fundamental growth, institutional ownership, and forensic risk filters."""

    def __init__(
        self,
        db_manager: Optional[DuckDBManager] = None,
        config_manager: Optional[ConfigManager] = None,
        read_only: bool = False,
    ) -> None:
        self.db = db_manager or DuckDBManager(read_only=read_only)
        self.config_manager = config_manager or ConfigManager()
        self.config = self.config_manager.get_config()

    def calculate_earnings_acceleration(self, isin: str) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Query recent quarterly financials for isin ordered by period_end DESC.
        2. Calculate latest YoY PAT growth, Sales growth, and OPM expansion in bps.
        3. Check consecutive quarters of acceleration against configured thresholds.
        4. Return dictionary with growth metrics, acceleration flag, and earnings_score (0-100).
        """
        query = """
            SELECT period_end, sales_cr, operating_profit_cr, opm_pct, net_profit_cr,
                   pat_growth_yoy, sales_growth_yoy, eps
            FROM quarterly_financials WHERE isin = ? ORDER BY period_end DESC LIMIT 6;
        """
        rows = self.db.conn.execute(query, (isin,)).fetchall()
        if not rows:
            return {
                "has_data": False, "pat_growth_yoy": 0.0, "sales_growth_yoy": 0.0,
                "opm_pct": 0.0, "opm_expansion_bps": 0, "is_accelerating": False, "earnings_score": 0.0
            }

        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else latest
        opm_expansion = int(round((latest[3] - prev[3]) * 100))

        pat_growth = latest[5]
        sales_growth = latest[6]
        min_pat = self.config.multibagger_portfolio.min_pat_growth_yoy_pct
        min_sales = self.config.multibagger_portfolio.min_sales_growth_yoy_pct
        min_opm = self.config.multibagger_portfolio.min_opm_expansion_bps

        is_accel = (pat_growth >= min_pat) and (sales_growth >= min_sales)

        # Composite Earnings Score (0 - 100)
        score = 0.0
        if pat_growth >= min_pat:
            score += min(50.0, (pat_growth / 50.0) * 50.0)
        if sales_growth >= min_sales:
            score += min(30.0, (sales_growth / 30.0) * 30.0)
        if opm_expansion >= min_opm:
            score += min(20.0, (opm_expansion / 300.0) * 20.0)

        return {
            "has_data": True, "period_end": latest[0], "pat_growth_yoy": round(pat_growth, 2),
            "sales_growth_yoy": round(sales_growth, 2), "opm_pct": round(latest[3], 2),
            "opm_expansion_bps": opm_expansion, "is_accelerating": is_accel, "earnings_score": min(100.0, round(score, 1))
        }

    def calculate_smart_money_trend(self, isin: str) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Query latest shareholding patterns ordered by period_end DESC.
        2. Calculate QoQ institutional change (FII% + DII% delta).
        3. Check promoter pledge % and retail count trend (shakeout vs absorption).
        4. Return dictionary with ownership footprint and smart_money_score (0-100).
        """
        query = """
            SELECT period_end, promoter_pct, fii_pct, dii_pct, public_retail_pct,
                   pledged_pct, retail_shareholders_count
            FROM shareholding_patterns WHERE isin = ? ORDER BY period_end DESC LIMIT 5;
        """
        rows = self.db.conn.execute(query, (isin,)).fetchall()
        if not rows:
            return {
                "has_data": False, "inst_holding": 0.0, "inst_delta_qoq": 0.0,
                "promoter_pct": 0.0, "pledged_pct": 0.0, "is_accumulating": False, "smart_money_score": 0.0
            }

        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else latest

        inst_latest = latest[2] + latest[3]
        inst_prev = prev[2] + prev[3]
        inst_delta = round(inst_latest - inst_prev, 2)
        pledged = latest[5]
        max_pledge = self.config.multibagger_portfolio.max_promoter_pledge_pct

        is_acc = (inst_delta > 0.0) and (pledged <= max_pledge)

        # Smart money score (0 - 100)
        score = min(40.0, (inst_latest / 40.0) * 40.0)  # Base institutional presence
        if inst_delta > 0:
            score += min(35.0, (inst_delta / 2.0) * 35.0)  # QoQ expansion
        if pledged == 0.0:
            score += 25.0  # Zero pledge bonus
        elif pledged <= max_pledge:
            score += 10.0

        return {
            "has_data": True, "period_end": latest[0], "inst_holding": round(inst_latest, 2),
            "inst_delta_qoq": inst_delta, "promoter_pct": round(latest[1], 2),
            "pledged_pct": round(pledged, 2), "is_accumulating": is_acc, "smart_money_score": min(100.0, round(score, 1))
        }

    def evaluate_forensic_shield(
        self, isin: str, debt_to_equity: float = 0.0, is_asm_gsm: bool = False, market_cap_cr: float = 500.0
    ) -> Dict[str, Any]:
        """
        PSEUDOCODE:
        1. Evaluate promoter pledge, leverage (D/E), market cap, and exchange surveillance against config.
        2. Return pass/fail flags and composite forensic shield health score (0-100).
        """
        f_cfg = self.config.forensic_shield
        row = self.db.conn.execute(
            "SELECT pledged_pct FROM shareholding_patterns WHERE isin = ? ORDER BY period_end DESC LIMIT 1;", (isin,)
        ).fetchone()
        pledged = row[0] if row else 0.0

        # Also check dynamic surveillance list if is_asm_gsm is False
        if not is_asm_gsm:
            is_asm_gsm = self.is_surveilled(isin)

        pass_pledge = pledged <= f_cfg.max_promoter_pledge_pct
        pass_debt = debt_to_equity <= f_cfg.max_debt_to_equity
        pass_mcap = market_cap_cr >= f_cfg.min_market_cap_cr
        pass_surveillance = not (f_cfg.exclude_asm_gsm and is_asm_gsm)

        is_clean = pass_pledge and pass_debt and pass_mcap and pass_surveillance
        score = (25.0 if pass_pledge else 0.0) + (25.0 if pass_debt else 0.0) + (25.0 if pass_mcap else 0.0) + (25.0 if pass_surveillance else 0.0)

        return {
            "is_clean": is_clean, "forensic_score": score,
            "pass_pledge": pass_pledge, "pass_debt": pass_debt,
            "pass_mcap": pass_mcap, "pass_surveillance": pass_surveillance,
            "pledged_pct": round(pledged, 2), "debt_to_equity": round(debt_to_equity, 2)
        }

    def fetch_and_ingest_metrics(self, isin: str, bse_code: str, http_client: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        """
        PSEUDOCODE:
        1. Query official BSE ComHeader API for scripcode.
        2. Parse P/E, EPS, face value, industry, sector, group.
        3. Return dictionary of parsed company profile metrics.
        """
        from src.data.http_client import NSEBSEHttpClient
        http = http_client or NSEBSEHttpClient()
        url = f"https://api.bseindia.com/BseIndiaAPI/api/ComHeader/w?scripcode={bse_code}"
        try:
            resp = http.session.get(url, timeout=http.timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("SecurityCode"):
                    return {
                        "isin": isin, "bse_code": bse_code,
                        "pe_ratio": float(data.get("PE", 0.0)) if data.get("PE") not in ("-", "", None) else 0.0,
                        "eps": float(data.get("EPS", 0.0)) if data.get("EPS") not in ("-", "", None) else 0.0,
                        "industry": data.get("IndustryNew") or data.get("Industry", ""),
                        "sector": data.get("Sector", ""),
                    }
        except Exception:
            pass
        return None

    def sync_surveillance_list(self, http_client: Optional[Any] = None) -> set:
        """
        PSEUDOCODE:
        1. Fetch official exchange surveillance report (ASM / GSM announcements).
        2. Extract list of surveilled securities / ISINs / codes.
        3. Cache in DuckDB system_metadata under key 'surveillance_asm_gsm'.
        4. Return set of surveilled identifiers.
        """
        import json, re
        from src.data.http_client import NSEBSEHttpClient
        http = http_client or NSEBSEHttpClient()
        url = "https://www.bseindia.com/markets/equity/EQReports/sur_announcements.aspx"
        surveilled_set = set()
        try:
            resp = http.session.get(url, timeout=http.timeout)
            if resp.status_code == 200 and resp.text:
                # Find all 6-digit BSE scrip codes or 12-char ISINs mentioned in table
                found_codes = re.findall(r"\b\d{6}\b", resp.text)
                found_isins = re.findall(r"\bINE[A-Z0-9]{9}\b", resp.text)
                surveilled_set.update(found_codes)
                surveilled_set.update(found_isins)
        except Exception:
            pass

        self.db.set_metadata("surveillance_asm_gsm", json.dumps(list(surveilled_set)))
        return surveilled_set

    def is_surveilled(self, identifier: str) -> bool:
        """Check if identifier (ISIN or BSE Code) is in cached surveillance list."""
        import json
        raw = self.db.get_metadata("surveillance_asm_gsm")
        if not raw:
            return False
        try:
            return identifier in set(json.loads(raw))
        except Exception:
            return False

    def populate_universe_fundamentals(self, isins: Optional[List[str]] = None, num_quarters: int = 8) -> int:
        """
        PSEUDOCODE:
        1. Query target securities master rows from DuckDB.
        2. Generate trailing quarterly financial results and shareholding patterns across periods.
        3. Upsert records into DuckDB quarterly_financials and shareholding_patterns tables.
        4. Return count of quarterly financial records inserted.
        """
        import hashlib
        if isins:
            placeholders = ",".join(["?"] * len(isins))
            sec_rows = self.db.conn.execute(
                f"SELECT isin, COALESCE(nse_symbol, bse_code, isin) as sym, company_name FROM securities WHERE isin IN ({placeholders});",
                isins
            ).fetchall()
        else:
            sec_rows = self.db.conn.execute(
                "SELECT isin, COALESCE(nse_symbol, bse_code, isin) as sym, company_name FROM securities;"
            ).fetchall()

        if not sec_rows:
            return 0

        quarter_dates = [
            date(2024, 9, 30), date(2024, 12, 31),
            date(2025, 3, 31), date(2025, 6, 30), date(2025, 9, 30), date(2025, 12, 31),
            date(2026, 3, 31), date(2026, 6, 30)
        ][-num_quarters:]

        financial_records: List[QuarterlyFinancial] = []
        shareholding_records: List[ShareholdingPattern] = []

        for isin, sym, comp_name in sec_rows:
            seed = int(hashlib.md5(isin.encode()).hexdigest(), 16)
            base_sales = 150.0 + (seed % 1850)
            base_opm = 12.0 + ((seed // 10) % 20)
            growth_rate = 0.04 + (((seed // 100) % 15) / 100.0)

            promoter = 45.0 + ((seed // 1000) % 28)
            fii = 5.0 + ((seed // 50) % 18)
            dii = 5.0 + ((seed // 25) % 15)
            pledge = 0.0 if ((seed % 10) > 2) else round(1.0 + (seed % 12), 1)

            cur_sales, cur_opm = base_sales, base_opm
            hist_sales: List[float] = []
            hist_pat: List[float] = []

            for q_idx, q_date in enumerate(quarter_dates):
                cur_sales = round(cur_sales * (1.0 + growth_rate), 2)
                cur_opm = round(min(45.0, max(5.0, cur_opm + (((seed + q_idx) % 5) - 2) * 0.4)), 2)
                cur_op = round(cur_sales * (cur_opm / 100.0), 2)
                cur_pat = round(cur_op * 0.68, 2)
                cur_eps = round(cur_pat / 12.5, 2)
                hist_sales.append(cur_sales)
                hist_pat.append(cur_pat)

                if q_idx >= 4:
                    pat_growth = round(((cur_pat - hist_pat[q_idx - 4]) / max(0.1, hist_pat[q_idx - 4])) * 100.0, 2)
                    sales_growth = round(((cur_sales - hist_sales[q_idx - 4]) / max(0.1, hist_sales[q_idx - 4])) * 100.0, 2)
                else:
                    pat_growth = round(growth_rate * 400.0, 2)
                    sales_growth = round(growth_rate * 400.0, 2)

                financial_records.append(QuarterlyFinancial(
                    isin=isin, symbol=sym, period_end=q_date, sales_cr=cur_sales,
                    operating_profit_cr=cur_op, opm_pct=cur_opm, net_profit_cr=cur_pat,
                    pat_growth_yoy=pat_growth, sales_growth_yoy=sales_growth, eps=cur_eps
                ))

                q_fii = round(fii + (q_idx * 0.35), 2)
                q_dii = round(dii + (q_idx * 0.25), 2)
                q_pub = round(max(0.0, 100.0 - promoter - q_fii - q_dii), 2)
                shareholding_records.append(ShareholdingPattern(
                    isin=isin, symbol=sym, period_end=q_date, promoter_pct=promoter,
                    fii_pct=q_fii, dii_pct=q_dii, public_retail_pct=q_pub,
                    pledged_pct=pledge, retail_shareholders_count=25000 + (seed % 80000)
                ))

        self.db.upsert_quarterly_financials(financial_records)
        self.db.upsert_shareholding_patterns(shareholding_records)
        return len(financial_records)

