"""Download recent annual reports from SEC EDGAR with a compliant contact header."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from src.config import settings

OUT = Path("data/filings")
TICKER_MAP = "https://www.sec.gov/files/company_tickers.json"


def _headers() -> dict[str, str]:
    if "@" not in settings.sec_user_agent or "example.com" in settings.sec_user_agent:
        raise ValueError("Set SEC_USER_AGENT in .env to identify this app and a real contact email.")
    return {"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"}


def get_cik_map(session: requests.Session | None = None) -> dict[str, str]:
    client = session or requests.Session()
    response = client.get(TICKER_MAP, headers=_headers(), timeout=30)
    response.raise_for_status()
    records: dict[str, Any] = response.json()
    return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in records.values()}


def list_10k(cik10: str, session: requests.Session | None = None) -> list[dict[str, str]]:
    client = session or requests.Session()
    response = client.get(
        f"https://data.sec.gov/submissions/CIK{cik10}.json", headers=_headers(), timeout=30
    )
    response.raise_for_status()
    recent = response.json()["filings"]["recent"]
    found = []
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            found.append({
                "accession": recent["accessionNumber"][i],
                "document": recent["primaryDocument"][i],
                "filing_date": recent["filingDate"][i],
                "report_date": recent["reportDate"][i],
            })
    return found


def download_filings(tickers: list[str] | None = None, count: int | None = None) -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    client = requests.Session()
    cik_map = get_cik_map(client)
    saved: list[Path] = []
    for ticker in tickers or settings.ticker_list:
        ticker = ticker.upper()
        if ticker not in cik_map:
            raise ValueError(f"Ticker {ticker} is not present in the SEC company ticker map")
        cik = cik_map[ticker]
        for filing in list_10k(cik, client)[: count or settings.filings_per_ticker]:
            acc_compact = filing["accession"].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_compact}/{filing['document']}"
            path = OUT / f"{ticker}_{filing['report_date']}_{filing['accession']}.html"
            if path.exists() and path.stat().st_size > 0:
                saved.append(path)
                continue
            response = client.get(url, headers=_headers(), timeout=60)
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "").lower() and not response.content:
                raise RuntimeError(f"SEC returned an empty filing for {ticker} {filing['report_date']}")
            path.write_bytes(response.content)
            saved.append(path)
            time.sleep(0.12)
    return saved


if __name__ == "__main__":
    paths = download_filings()
    print(json.dumps({"downloaded_or_cached": len(paths), "files": [str(p) for p in paths]}, indent=2))
