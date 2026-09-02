"""
Module: src/data/http_client.py
Purpose: HTTP client for official NSE and BSE archive endpoints with browser headers.
"""
from datetime import date
from typing import Optional
import requests


class NSEBSEHttpClient:
    """Handles HTTP network requests to official exchange archives with anti-bot headers."""

    NSE_MASTER_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    NSE_BHAVCOPY_URL_TEMPLATE = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddMMyyyy}.csv"
    BSE_MASTER_URL = "https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active"

    def __init__(self, timeout_seconds: int = 15) -> None:
        self.timeout = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.bseindia.com/",
        })

    def fetch_nse_master(self) -> Optional[str]:
        """
        PSEUDOCODE:
        1. Send GET request to NSE_MASTER_URL with configured browser session.
        2. If status code is 200, return response text; otherwise return None.
        """
        try:
            resp = self.session.get(self.NSE_MASTER_URL, timeout=self.timeout)
            return resp.text if resp.status_code == 200 else None
        except requests.RequestException:
            return None

    def fetch_nse_bhavcopy(self, trade_date: date) -> Optional[str]:
        """
        PSEUDOCODE:
        1. Format trade_date as ddMMyyyy (e.g., 01092026).
        2. Construct URL from NSE_BHAVCOPY_URL_TEMPLATE.
        3. Send GET request; return CSV text if 200 OK, else None.
        """
        formatted_date = trade_date.strftime("%d%m%Y")
        url = self.NSE_BHAVCOPY_URL_TEMPLATE.format(ddMMyyyy=formatted_date)
        try:
            resp = self.session.get(url, timeout=self.timeout)
            return resp.text if resp.status_code == 200 else None
        except requests.RequestException:
            return None

    def fetch_bse_master(self) -> Optional[str]:
        """
        PSEUDOCODE:
        1. Send GET request to BSE_MASTER_URL (JSON endpoint).
        2. Return raw JSON string if successful, else None.
        """
        try:
            resp = self.session.get(self.BSE_MASTER_URL, timeout=self.timeout)
            return resp.text if resp.status_code == 200 else None
        except requests.RequestException:
            return None

