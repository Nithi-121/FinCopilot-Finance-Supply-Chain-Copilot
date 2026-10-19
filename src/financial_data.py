"""Download SEC XBRL company facts and build auditable annual finance tables."""
from __future__ import annotations

import itertools
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import requests

from src.config import settings
from src.fetch import _headers, get_cik_map

FACTS_DIR = Path("data/companyfacts")
DB_PATH = Path(settings.fincopilot_db_path)
FACT_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TAGS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues"],
    "gross profit": ["GrossProfit"],
    "operating income": ["OperatingIncomeLoss"],
    "net income": ["NetIncomeLoss"],
    "operating cash flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def fetch_companyfacts(tickers: list[str] | None = None) -> list[Path]:
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    requested = tickers or settings.ticker_list
    missing = [ticker for ticker in requested if not (FACTS_DIR / f"{ticker}.json").exists()]
    ciks = get_cik_map(session) if missing else {}
    saved = []
    for ticker in requested:
        path = FACTS_DIR / f"{ticker}.json"
        if path.exists():
            saved.append(path)
            continue
        if ticker not in ciks:
            raise ValueError(f"Ticker {ticker} is not present in the SEC company ticker map")
        response = session.get(FACT_URL.format(cik=ciks[ticker]), headers=_headers(), timeout=60)
        response.raise_for_status()
        path.write_bytes(response.content)
        time.sleep(0.12)
        saved.append(path)
    return saved


def _annual_values(facts: dict[str, Any], ticker: str, metric: str, tags: list[str]) -> list[dict[str, Any]]:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    selected: dict[str, dict[str, Any]] = {}
    for tag in tags:
        fact = us_gaap.get(tag)
        if not fact:
            continue
        units = fact.get("units", {})
        unit_key = next((u for u in units if ("/share" in u.lower() or "shares" in u.lower()) == (metric == "eps")), None)
        if unit_key is None:
            continue
        for entry in units[unit_key]:
            if entry.get("form") != "10-K" or not entry.get("fy") or not entry.get("end"):
                continue
            start = entry.get("start")
            if start:
                days = (np.datetime64(entry["end"]) - np.datetime64(start)).astype(int)
                if not 300 <= int(days) <= 400:
                    continue
            elif metric != "eps":
                continue
            # The fact's end date identifies the actual comparative period. The
            # filing-level `fy` field can label prior-year comparative facts with
            # the current filing year, so it is not used as the observation key.
            period = str(entry["end"][:4])
            candidate = {
                "company": ticker,
                "metric": metric,
                "period": period,
                "value": float(entry["val"]),
                "unit": unit_key,
                "source": f"us-gaap:{tag}",
                "accession": entry.get("accn", ""),
            }
            # Prefer the most recently filed annual value for a restated period.
            current = selected.get(period)
            if current is None or entry.get("filed", "") > current.get("filed", ""):
                candidate["filed"] = entry.get("filed", "")
                selected[period] = candidate
    return [{k: v for k, v in value.items() if k != "filed"} for _, value in sorted(selected.items())]


def extract_financials(paths: list[Path] | None = None) -> list[dict[str, Any]]:
    records = []
    for path in paths or sorted(FACTS_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        ticker = path.stem.upper()
        for metric, tags in TAGS.items():
            records.extend(_annual_values(payload, ticker, metric, tags))
    if not records:
        raise ValueError("No annual values were extracted from company facts")
    records = add_margin_metrics(records)
    # The normalized source file itself makes unit/tag coverage reviewable.
    (Path("data")).mkdir(exist_ok=True)
    Path("data/annual_financials.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return records


def add_margin_metrics(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calculate gross and operating margin from reported XBRL values, never from the LLM."""
    by_period: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in records:
        by_period.setdefault((row["company"], row["period"]), {})[row["metric"]] = row
    derived = []
    for values in by_period.values():
        revenue = values.get("revenue")
        if not revenue or not revenue["value"]:
            continue
        for source_metric, target_metric in (("gross profit", "gross margin"), ("operating income", "operating margin")):
            base = values.get(source_metric)
            if not base:
                continue
            derived.append({
                **revenue,
                "metric": target_metric,
                "value": round(float(base["value"]) / float(revenue["value"]) * 100, 6),
                "unit": "%",
                "source": f"calculated: {source_metric} / revenue * 100 ({base['source']}; {revenue['source']})",
            })
    return records + derived


def _predict(train: list[dict[str, Any]], year: int, model: str) -> float:
    values = np.array([r["value"] for r in train], dtype=float)
    if model == "last_value":
        return float(values[-1])
    years = np.array([int(r["period"]) for r in train], dtype=float)
    slope, intercept = np.polyfit(years, values, 1)
    return float(slope * year + intercept)


def forecast_series(series: list[dict[str, Any]], min_points: int = 6) -> dict[str, Any] | None:
    """Choose last-value or linear trend using rolling one-step-ahead MAE."""
    ordered = sorted(series, key=lambda r: int(r["period"]))
    if len(ordered) < min_points:
        return None
    candidates = {"last_value": [], "linear_trend": []}
    # Hold out up to the final five annual periods; each prediction uses prior years only.
    first_test = max(3, len(ordered) - 5)
    for i in range(first_test, len(ordered)):
        train = ordered[:i]
        actual = float(ordered[i]["value"])
        for model, errors_for_model in candidates.items():
            estimate = _predict(train, int(ordered[i]["period"]), model)
            errors_for_model.append((actual, estimate))
    errors = {
        model: {
            "mae": float(np.mean([abs(a - p) for a, p in pairs])),
            "mape": float(np.mean([abs(a - p) / abs(a) for a, p in pairs if a != 0]) * 100),
        }
        for model, pairs in candidates.items()
    }
    chosen = min(errors, key=lambda name: errors[name]["mae"])
    latest_year = int(ordered[-1]["period"])
    next_year = latest_year + 1
    prediction = _predict(ordered, next_year, chosen)
    if not math.isfinite(prediction):
        return None
    base = ordered[-1]
    return {
        "company": base["company"], "metric": base["metric"], "period": str(next_year),
        "value": prediction, "unit": base["unit"], "model": chosen,
        "mae": errors[chosen]["mae"], "mape": errors[chosen]["mape"],
        "last_value_mae": errors["last_value"]["mae"], "last_value_mape": errors["last_value"]["mape"],
        "linear_trend_mae": errors["linear_trend"]["mae"], "linear_trend_mape": errors["linear_trend"]["mape"],
        "training_periods": len(ordered), "source": "SEC company facts; rolling one-step backtest",
    }


def build_forecasts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault((row["company"], row["metric"]), []).append(row)
    return [row for series in grouped.values() if (row := forecast_series(series)) is not None]


def find_anomalies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault((row["company"], row["metric"]), []).append(row)
    anomalies = []
    for series in grouped.values():
        ordered = sorted(series, key=lambda r: int(r["period"]))
        if len(ordered) < 6:
            continue
        changes = []
        for previous, current in itertools.pairwise(ordered):
            before = float(previous["value"])
            after = float(current["value"])
            if current["unit"] == "%":
                changes.append((current, after - before, "percentage points"))
            elif before > 0:
                changes.append((current, (after / before - 1) * 100, "% year over year"))
            else:
                changes.append((current, after - before, current["unit"]))
        values = [change for _, change, _ in changes]
        median = statistics.median(values)
        mad = statistics.median(abs(x - median) for x in values)
        if mad == 0:
            continue
        for row, change, change_unit in changes:
            robust_z = 0.6745 * (change - median) / mad
            if abs(robust_z) >= 3.5:
                anomalies.append({**row, "change": change, "change_unit": change_unit,
                                  "robust_z": robust_z, "method": "year-over-year change; median absolute deviation"})
    return anomalies


def build_database(records: list[dict[str, Any]]) -> dict[str, int]:
    forecasts = build_forecasts(records)
    anomalies = find_anomalies(records)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(DB_PATH)) as con:
        con.execute("CREATE OR REPLACE TABLE financials (company VARCHAR, metric VARCHAR, period VARCHAR, value DOUBLE, unit VARCHAR, source VARCHAR, accession VARCHAR)")
        con.execute("CREATE OR REPLACE TABLE forecasts (company VARCHAR, metric VARCHAR, period VARCHAR, value DOUBLE, unit VARCHAR, model VARCHAR, mae DOUBLE, mape DOUBLE, last_value_mae DOUBLE, last_value_mape DOUBLE, linear_trend_mae DOUBLE, linear_trend_mape DOUBLE, training_periods INTEGER, source VARCHAR)")
        con.execute("CREATE OR REPLACE TABLE anomalies (company VARCHAR, metric VARCHAR, period VARCHAR, value DOUBLE, unit VARCHAR, source VARCHAR, accession VARCHAR, change DOUBLE, change_unit VARCHAR, robust_z DOUBLE, method VARCHAR)")
        con.executemany("INSERT INTO financials VALUES (?, ?, ?, ?, ?, ?, ?)", [tuple(r.get(k) for k in ("company", "metric", "period", "value", "unit", "source", "accession")) for r in records])
        if forecasts:
            con.executemany("INSERT INTO forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [tuple(r.get(k) for k in ("company", "metric", "period", "value", "unit", "model", "mae", "mape", "last_value_mae", "last_value_mape", "linear_trend_mae", "linear_trend_mape", "training_periods", "source")) for r in forecasts])
        if anomalies:
            con.executemany("INSERT INTO anomalies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [tuple(r.get(k) for k in ("company", "metric", "period", "value", "unit", "source", "accession", "change", "change_unit", "robust_z", "method")) for r in anomalies])
    return {"financials": len(records), "forecasts": len(forecasts), "anomalies": len(anomalies)}


def build_all() -> dict[str, int]:
    files = fetch_companyfacts()
    records = extract_financials(files)
    return build_database(records)


if __name__ == "__main__":
    print(json.dumps(build_all(), indent=2))
