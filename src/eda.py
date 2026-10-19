"""Create reproducible data-coverage, trend, forecast, and anomaly summaries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from src.config import settings

OUTPUT = Path("reports")
FIGURES = OUTPUT / "figures"
TABLES = OUTPUT / "tables"


def _read_tables(database: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with duckdb.connect(database, read_only=True) as connection:
        names = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        required = {"financials", "forecasts", "anomalies"}
        missing = required - names
        if missing:
            raise RuntimeError(f"The configured database is missing tables: {sorted(missing)}")
        return tuple(
            connection.execute(f'SELECT * FROM "{name}"').fetchdf()
            for name in ("financials", "forecasts", "anomalies")
        )


def _write_coverage(financials: pd.DataFrame) -> pd.DataFrame:
    data = financials.copy()
    data["period_num"] = pd.to_numeric(data["period"], errors="coerce")
    coverage = (
        data.groupby(["company", "metric"], as_index=False)
        .agg(
            observation_count=("value", "count"),
            first_period=("period_num", "min"),
            last_period=("period_num", "max"),
            unit=("unit", "first"),
        )
        .sort_values(["company", "metric"])
    )
    coverage[["first_period", "last_period"]] = coverage[["first_period", "last_period"]].astype("Int64")
    coverage.to_csv(TABLES / "financial_data_coverage.csv", index=False)
    return coverage


def _plot_financial_trends(financials: pd.DataFrame) -> None:
    metrics = financials[financials["metric"].isin(["revenue", "operating margin"])].copy()
    metrics["period_num"] = pd.to_numeric(metrics["period"], errors="coerce")
    metrics = metrics.dropna(subset=["period_num", "value"])
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.4), constrained_layout=True)
    palette = dict(zip(sorted(metrics["company"].unique()), plt.get_cmap("tab10").colors))
    for company, rows in metrics[metrics["metric"] == "revenue"].groupby("company"):
        rows = rows.sort_values("period_num")
        axes[0].plot(rows["period_num"], rows["value"] / 1e9, marker="o", linewidth=2, label=company, color=palette[company])
    for company, rows in metrics[metrics["metric"] == "operating margin"].groupby("company"):
        rows = rows.sort_values("period_num")
        axes[1].plot(rows["period_num"], rows["value"], marker="o", linewidth=2, label=company, color=palette[company])
    axes[0].set_title("Annual reported revenue")
    axes[0].set_ylabel("USD billions")
    axes[1].set_title("Annual operating margin")
    axes[1].set_ylabel("Percent")
    years = sorted(metrics["period_num"].dropna().unique())
    tick_step = max(1, (len(years) + 7) // 8)
    year_ticks = years[::tick_step]
    if years and year_ticks[-1] != years[-1]:
        if years[-1] - year_ticks[-1] < tick_step:
            year_ticks[-1] = years[-1]
        else:
            year_ticks.append(years[-1])
    for axis in axes:
        axis.set_xlabel("Fiscal year from SEC fact period")
        axis.grid(True, alpha=0.25)
        axis.legend(title="Company", frameon=False)
        axis.set_xticks(year_ticks)
    fig.suptitle("Public-company financial trends", fontsize=15, fontweight="bold")
    fig.savefig(FIGURES / "financial_trends.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def _plot_forecast_comparison(forecasts: pd.DataFrame) -> pd.DataFrame:
    columns = ["last_value_mape", "linear_trend_mape"]
    for column in columns:
        forecasts[column] = pd.to_numeric(forecasts[column], errors="coerce")
    summary = (
        forecasts.groupby("metric", as_index=False)
        .agg(
            series_count=("company", "count"),
            last_value_median_mape_percent=("last_value_mape", "median"),
            linear_trend_median_mape_percent=("linear_trend_mape", "median"),
            selected_last_value=("model", lambda values: (values == "last_value").sum()),
            selected_linear_trend=("model", lambda values: (values == "linear_trend").sum()),
        )
        .sort_values("metric")
    )
    summary.to_csv(TABLES / "forecast_model_comparison.csv", index=False)
    fig, axis = plt.subplots(figsize=(10.5, 5.6), constrained_layout=True)
    positions = range(len(summary))
    width = 0.36
    axis.bar(
        [position - width / 2 for position in positions],
        summary["last_value_median_mape_percent"], width,
        label="Last-value baseline", color="#147D92",
    )
    axis.bar(
        [position + width / 2 for position in positions],
        summary["linear_trend_median_mape_percent"], width,
        label="Linear trend", color="#E5963B",
    )
    axis.set_title(
        "Walk-forward forecast error by metric\n"
        "Lower MAPE is better; model selection per series uses MAE.",
        fontsize=12,
        pad=12,
    )
    axis.set_ylabel("Median MAPE (%) across company series")
    axis.set_xticks(list(positions), summary["metric"], rotation=25, ha="right")
    axis.legend(frameon=False)
    axis.grid(axis="y", alpha=0.25)
    fig.savefig(FIGURES / "forecast_model_comparison.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    return summary


def _plot_anomalies(anomalies: pd.DataFrame) -> pd.DataFrame:
    counts = (
        anomalies.groupby(["company", "metric"]).size().rename("candidate_count")
        .reset_index()
    )
    counts.to_csv(TABLES / "anomaly_candidate_counts.csv", index=False)
    matrix = counts.pivot(index="company", columns="metric", values="candidate_count").fillna(0)
    fig, axis = plt.subplots(figsize=(10, 4.8), constrained_layout=True)
    if matrix.empty:
        axis.text(0.5, 0.5, "No candidate anomalies in this snapshot", ha="center", va="center")
        axis.set_axis_off()
    else:
        image = axis.imshow(matrix.to_numpy(), cmap="YlOrRd", aspect="auto")
        axis.set_xticks(range(len(matrix.columns)), matrix.columns, rotation=35, ha="right")
        axis.set_yticks(range(len(matrix.index)), matrix.index)
        axis.set_title("Candidate anomaly counts by company and metric")
        for row in range(matrix.shape[0]):
            for column in range(matrix.shape[1]):
                value = int(matrix.iat[row, column])
                if value:
                    axis.text(column, row, str(value), ha="center", va="center", color="#222222")
        fig.colorbar(image, ax=axis, label="Candidate count")
    fig.savefig(FIGURES / "anomaly_candidates.png", dpi=180, bbox_inches="tight")
    plt.close(fig)
    return counts


def build_analysis(database: str | None = None) -> dict[str, Any]:
    """Read the project database and save portfolio-ready tables and charts."""
    db_path = database or settings.duckdb_path or settings.fincopilot_db_path
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"Financial database not found: {db_path}. Run the financial data pipeline first.")
    FIGURES.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    financials, forecasts, anomalies = _read_tables(db_path)
    coverage = _write_coverage(financials)
    _plot_financial_trends(financials)
    forecast_summary = _plot_forecast_comparison(forecasts.copy())
    anomaly_counts = _plot_anomalies(anomalies)
    summary = {
        "database": db_path,
        "financial_rows": len(financials),
        "company_count": int(financials["company"].nunique()),
        "metric_count": int(financials["metric"].nunique()),
        "forecast_series": len(forecasts),
        "anomaly_candidates": len(anomalies),
        "companies": sorted(financials["company"].dropna().unique().tolist()),
        "coverage_rows": len(coverage),
        "anomaly_count_groups": len(anomaly_counts),
        "forecast_mape_comparison": json.loads(forecast_summary.to_json(orient="records")),
        "interpretation_note": "Forecasts and anomaly candidates are exploratory baselines, not investment advice or proof of future performance.",
        "outputs": [
            "reports/figures/financial_trends.png",
            "reports/figures/forecast_model_comparison.png",
            "reports/figures/anomaly_candidates.png",
            "reports/tables/financial_data_coverage.csv",
            "reports/tables/forecast_model_comparison.csv",
            "reports/tables/anomaly_candidate_counts.csv",
        ],
    }
    (OUTPUT / "analysis_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(build_analysis(), indent=2))
