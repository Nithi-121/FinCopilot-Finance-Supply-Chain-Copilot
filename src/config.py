"""Central configuration; secrets and machine-specific paths stay in the environment."""
import os
from dataclasses import dataclass
from pathlib import Path


def _read_env_file() -> None:
    """Load simple KEY=value entries without overriding process environment variables."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_read_env_file()


@dataclass
class Settings:
    sec_user_agent: str = os.getenv("SEC_USER_AGENT", "")
    tickers: str = os.getenv("TICKERS", "HPE,MSFT,AAPL,NVDA,ACN")
    filings_per_ticker: int = int(os.getenv("FILINGS_PER_TICKER", "3"))
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    # The 1.5B quantized model is a practical default for this 8 GB RAM machine.
    # Users with more memory can select a larger Qwen2.5 tag in .env.
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
    ollama_timeout_seconds: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "180"))
    duckdb_path: str = os.getenv("DUCKDB_PATH", "")
    fincopilot_db_path: str = os.getenv("FINCOPILOT_DB_PATH", "data/fincopilot.duckdb")
    financials_table: str = os.getenv("FINANCIALS_TABLE", "financials")
    forecasts_table: str = os.getenv("FORECASTS_TABLE", "forecasts")
    anomalies_table: str = os.getenv("ANOMALIES_TABLE", "anomalies")
    chroma_path: str = os.getenv("CHROMA_PATH", "data/index/chroma")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")
    top_k: int = int(os.getenv("TOP_K", "3"))

    @property
    def ticker_list(self) -> list[str]:
        return [item.strip().upper() for item in self.tickers.split(",") if item.strip()]


settings = Settings()
