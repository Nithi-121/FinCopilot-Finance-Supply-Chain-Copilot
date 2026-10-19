"""Read-only financial tools. SQL identifiers are allowlisted; values are parameterized."""
from __future__ import annotations

import re
from typing import Any

import duckdb

from src.config import settings
from src.retrieve import retrieve


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Table names may contain only letters, digits, and underscores")
    return f'"{value}"'


def _connect():
    db_path = settings.duckdb_path or settings.fincopilot_db_path
    if not db_path:
        raise RuntimeError("DuckDB is not configured. Set FINCOPILOT_DB_PATH or DUCKDB_PATH in .env.")
    return duckdb.connect(db_path, read_only=True)


def _query(table: str, company: str, metric: str, limit: int = 8) -> list[dict[str, Any]]:
    safe_table = _identifier(table)
    with _connect() as con:
        columns = {row[0] for row in con.execute(f"DESCRIBE {safe_table}").fetchall()}
        required = {"company", "metric"}
        if not required.issubset(columns):
            raise RuntimeError(f"Table {table} must contain columns {sorted(required)}; found {sorted(columns)}")
        period_column = next((x for x in ("period", "fiscal_period", "date", "fiscal_year") if x in columns), None)
        value_column = next((x for x in ("value", "amount", "forecast", "yhat") if x in columns), None)
        if not period_column or not value_column:
            raise RuntimeError(f"Table {table} needs a period and value column; found {sorted(columns)}")
        sql = f"SELECT * FROM {safe_table} WHERE upper(company)=upper(?) AND lower(metric)=lower(?) ORDER BY \"{period_column}\" DESC LIMIT ?"
        return con.execute(sql, [company, metric, limit]).fetchdf().to_dict("records")


def get_financials(company: str, metric: str, periods: int = 4) -> list[dict[str, Any]]:
    return _query(settings.financials_table, company, metric, max(1, min(periods, 20)))


def get_forecast(company: str, metric: str, horizon: int = 4) -> list[dict[str, Any]]:
    return _query(settings.forecasts_table, company, metric, max(1, min(horizon, 20)))


def get_anomalies(company: str, metric: str) -> list[dict[str, Any]]:
    return _query(settings.anomalies_table, company, metric, 20)


def search_filings(query: str, company: str | None = None, year: str | None = None, k: int = 5) -> list[dict]:
    """Search indexed filing text and return source-tagged passages."""
    return retrieve(query, company=company, year=year, k=k)
