"""Write an auditable, content-hashed manifest for the locally downloaded filing snapshot."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

FILINGS = Path("data/filings")
OUT = Path("data/filing_manifest.json")


def build_manifest() -> dict:
    files = []
    for path in sorted(FILINGS.glob("*.html")):
        ticker, report_date, accession = path.stem.split("_", 2)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({
            "ticker": ticker,
            "report_date": report_date,
            "accession": accession,
            "filename": path.name,
            "bytes": path.stat().st_size,
            "sha256": digest,
        })
    manifest = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "source": "SEC EDGAR company submissions and filing archives",
        "filing_count": len(files),
        "files": files,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    result = build_manifest()
    print(f"Manifest written: {result['filing_count']} filings -> {OUT}")
