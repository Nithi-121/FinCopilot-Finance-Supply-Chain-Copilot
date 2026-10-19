"""FastAPI interface for the FinCopilot assistant."""
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.agent import ask
from src.config import settings

app = FastAPI(title="FinCopilot", version="0.1.0", description="Evidence-grounded SEC filing answers")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8502", "http://localhost:8502"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    company: str | None = None
    year: str | None = None
    top_k: int = Field(default=3, ge=1, le=10)
    retrieval_mode: Literal["hybrid", "dense", "bm25"] = "bm25"
    compare_company: str | None = None
    conversation_history: list[ChatTurn] = Field(default_factory=list, max_length=12)


@app.get("/health")
def health():
    """Liveness check: confirms that the API process is responding."""
    return {"status": "ok"}


@app.get("/ready")
def readiness():
    """Readiness check for the local index, financial database, and Ollama model."""
    db_path = settings.duckdb_path or settings.fincopilot_db_path
    checks = {
        "filing_index": Path(settings.chroma_path).is_dir(),
        "financial_database": bool(db_path) and Path(db_path).is_file(),
        "ollama_service": False,
        "ollama_model": False,
    }
    details = {}
    try:
        response = httpx.get(f"{settings.ollama_host.rstrip('/')}/api/tags", timeout=2.0)
        response.raise_for_status()
        checks["ollama_service"] = True
        models = response.json().get("models", [])
        checks["ollama_model"] = any(
            model.get("name") == settings.ollama_model for model in models
        )
    except (httpx.HTTPError, ValueError) as exc:
        details["ollama_error"] = str(exc)
    details["missing"] = [name for name, ready in checks.items() if not ready]
    return {"status": "ready" if all(checks.values()) else "not_ready", "checks": checks, "details": details}


@app.post("/ask")
def ask_endpoint(request: AskRequest):
    try:
        return ask(
            request.question,
            request.company,
            request.year,
            request.top_k,
            request.retrieval_mode,
            request.compare_company,
            [turn.model_dump() for turn in request.conversation_history],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail="The local Ollama service is unavailable. Start Ollama and verify GET /ready.",
        ) from exc
    except Exception as exc:
        # Ollama reports a missing local model as a response error rather than an HTTPX error.
        import ollama

        if isinstance(exc, (ollama.RequestError, ollama.ResponseError)):
            if settings.ollama_model.endswith(":cloud"):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The configured Ollama cloud model could not answer. Run `ollama signin`, "
                        "then retry and check account usage."
                    ),
                ) from exc
            raise HTTPException(
                status_code=503,
                detail="The configured local Ollama model is unavailable. Pull the model and verify GET /ready.",
            ) from exc
        raise HTTPException(status_code=500, detail=f"Could not answer the question: {exc}") from exc


@app.get("/analytics")
def analytics(company: str | None = None, metric: str = "revenue"):
    """Return chart-ready historical, forecast, and anomaly rows from read-only DuckDB."""
    import duckdb

    db_path = settings.duckdb_path or settings.fincopilot_db_path
    try:
        with duckdb.connect(db_path, read_only=True) as con:
            params: list[str] = []
            filter_sql = "1=1"
            if metric.lower() != "all":
                filter_sql += " AND lower(metric)=lower(?)"
                params.append(metric)
            if company:
                filter_sql += " AND upper(company)=upper(?)"
                params.append(company)
            history = con.execute(
                f"SELECT company, metric, period, value, unit, accession FROM financials WHERE {filter_sql} ORDER BY company, period",
                params,
            ).fetchdf().to_dict("records")
            forecasts = con.execute(
                f"SELECT company, metric, period, value, unit, model, mae, mape, last_value_mae, linear_trend_mae, last_value_mape, linear_trend_mape FROM forecasts WHERE {filter_sql} ORDER BY company, period",
                params,
            ).fetchdf().to_dict("records")
            anomalies = con.execute(
                f"SELECT company, metric, period, value, unit, change, change_unit, robust_z, method, accession FROM anomalies WHERE {filter_sql} ORDER BY company, period",
                params,
            ).fetchdf().to_dict("records")
        return {"history": history, "forecasts": forecasts, "anomalies": anomalies}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Financial analytics are unavailable: {exc}") from exc
