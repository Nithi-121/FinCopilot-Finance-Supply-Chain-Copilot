"""Small offline checks for stable project behavior."""

import pytest

from src.fetch import _headers
from src.financial_data import _annual_values, add_margin_metrics, find_anomalies, forecast_series
from src.generate import redact_unsupported_financial_figures, unsupported_financial_figures
from src.parse import chunk_text, split_sections
from src.tools import _identifier


def test_chunk_overlap_and_reconstruction():
    words = [f"w{i}" for i in range(1500)]
    chunks = chunk_text(" ".join(words), size=100, overlap=10)
    assert chunks[0].split()[-10:] == chunks[1].split()[:10]
    assert len(chunks) > 1


def test_section_split_finds_mda():
    text = "Item 1 Business " + "business details " * 60 + "Item 7 Management's Discussion and Analysis of Financial Condition and Results of Operations " + "results " * 80
    sections = split_sections(text)
    assert any(name == "Item 7 MD&A" for name, _ in sections)


def test_table_identifier_rejects_sql_injection():
    with pytest.raises(ValueError):
        _identifier("financials; DROP TABLE users")


def test_sec_header_requires_real_contact(monkeypatch):
    monkeypatch.setattr("src.fetch.settings.sec_user_agent", "FinCopilot person@example.org")
    assert "User-Agent" in _headers()
    monkeypatch.setattr("src.fetch.settings.sec_user_agent", "")
    with pytest.raises(ValueError):
        _headers()


def test_financial_number_guard_ignores_year_and_citation_ids():
    answer = "Revenue was $1,234 [ACN-2025-item-8-0001]."
    assert unsupported_financial_figures(answer, {"value": 1234}) == []
    assert unsupported_financial_figures("Revenue was $9,876.", {"value": 1234}) == ["9876"]


def test_unsupported_figures_are_redacted_without_dropping_the_answer():
    answer = "Revenue increased to $9,876 in 2025 [ACN-2025-item-8-0001]."
    safe = redact_unsupported_financial_figures(answer, {"value": 1234})
    assert "Revenue increased" in safe
    assert "[figure not verified]" in safe
    assert "$9,876" not in safe
    assert "2025" in safe
    assert "[ACN-2025-item-8-0001]" in safe


def test_answer_keeps_cited_explanation_when_financial_figure_is_unverified(monkeypatch):
    from src import generate

    class FakeClient:
        seen_messages = None

        def __init__(self, **kwargs):
            pass

        def chat(self, **kwargs):
            FakeClient.seen_messages = kwargs["messages"]
            return {"message": {"content": "Accenture described these risks, which rose 45% [ACN-risk-1]."}}

    monkeypatch.setattr(generate.ollama, "Client", FakeClient)
    answer = generate.generate_answer(
        "Summarize Accenture's risks",
        [{"id": "ACN-risk-1", "company": "ACN", "fiscal_year": "2025", "section": "Item 1A", "text": "Risk context"}],
        {},
    )
    assert "Accenture described these risks" in answer
    assert "[figure not verified]" in answer
    assert "$45" not in answer and "45%" not in answer
    assert "[ACN-risk-1]" in answer
    assert "try asking for a metric" in answer
    system_prompt = " ".join(FakeClient.seen_messages[0]["content"].split())
    assert "clear everyday English" in system_prompt
    assert "revenue means sales before expenses" in system_prompt


def test_forecast_selects_linear_trend_when_walk_forward_error_is_lower():
    series = [
        {"company": "ACN", "metric": "revenue", "period": str(year), "value": (year - 2015) * 100.0,
         "unit": "USD", "source": "fixture", "accession": "test"}
        for year in range(2015, 2023)
    ]
    result = forecast_series(series)
    assert result is not None
    assert result["model"] == "linear_trend"
    assert result["period"] == "2023"
    assert result["value"] == pytest.approx(800.0)


def test_company_fact_period_uses_fact_end_not_filing_year():
    facts = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerExcludingAssessedTax": {
        "units": {"USD": [{"form": "10-K", "fy": 2025, "start": "2023-07-01", "end": "2024-06-30",
                            "val": 100, "filed": "2024-08-01", "accn": "sample"}]}
    }}}}
    rows = _annual_values(facts, "MSFT", "revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax"])
    assert rows[0]["period"] == "2024"


def test_margin_metrics_are_derived_from_sec_facts():
    base = [
        {"company": "ACN", "metric": "revenue", "period": "2025", "value": 1000.0, "unit": "USD", "source": "sales", "accession": "x"},
        {"company": "ACN", "metric": "gross profit", "period": "2025", "value": 400.0, "unit": "USD", "source": "gross", "accession": "x"},
        {"company": "ACN", "metric": "operating income", "period": "2025", "value": 200.0, "unit": "USD", "source": "op", "accession": "x"},
    ]
    margins = {r["metric"]: r["value"] for r in add_margin_metrics(base) if "margin" in r["metric"]}
    assert margins == {"gross margin": 40.0, "operating margin": 20.0}


def test_anomaly_detection_flags_unusual_growth_not_trending_level():
    values = [100, 110, 121, 133, 146, 160, 176, 300]
    series = [
        {"company": "NVDA", "metric": "revenue", "period": str(2015 + i), "value": float(value),
         "unit": "USD", "source": "fixture", "accession": "test"}
        for i, value in enumerate(values)
    ]
    anomalies = find_anomalies(series)
    assert [r["period"] for r in anomalies] == ["2022"]


def test_conversational_questions_keep_context_without_forcing_filing_search(monkeypatch):
    from src import agent

    turns = [{"role": "user", "content": "What does this project use for search?"}]
    received = {}

    def unexpected_retrieval(*args, **kwargs):
        raise AssertionError("General conversation should not trigger filing retrieval")

    def answer(question, chunks, tools, history):
        received["history"] = history
        return "FinCopilot uses hybrid semantic and keyword search."

    monkeypatch.setattr(agent, "retrieve_with_ranks", unexpected_retrieval)
    monkeypatch.setattr(agent, "generate_answer", answer)
    result = agent.ask("Hi, can you explain that?", conversation_history=turns)

    assert result["answer_type"] == "conversational"
    assert result["citations"] == []
    assert received["history"] == turns


def test_api_accepts_short_natural_greeting():
    from api.main import AskRequest

    request = AskRequest(question="Hi")
    assert request.question == "Hi"
    assert request.top_k == 3
    assert request.retrieval_mode == "bm25"


def test_plain_finance_definition_does_not_trigger_filing_search(monkeypatch):
    from src import agent

    def unexpected_retrieval(*args, **kwargs):
        raise AssertionError("A general definition should not search company filings")

    monkeypatch.setattr(agent, "retrieve_with_ranks", unexpected_retrieval)
    monkeypatch.setattr(agent, "generate_answer", lambda *args: "Revenue is money from sales before costs.")
    result = agent.ask("What does revenue mean in simple words?")

    assert result["answer_type"] == "conversational"
    assert result["citations"] == []


def test_bm25_retrieval_does_not_load_the_embedding_model(monkeypatch):
    from src import retrieve

    rows = [
        {"id": "ACN-low", "company": "ACN", "fiscal_year": "2025", "text": "Risk discussion"},
        {"id": "ACN-high", "company": "ACN", "fiscal_year": "2025", "text": "Revenue discussion"},
    ]
    seen = {}

    class FakeBM25:
        def get_scores(self, tokens):
            return [1.0, 2.0]

    def load(*, with_model=True):
        seen["with_model"] = with_model
        return rows, FakeBM25(), None

    monkeypatch.setattr(retrieve, "_load", load)
    result = retrieve.retrieve_with_ranks("revenue", company="ACN", mode="bm25", k=1)

    assert seen["with_model"] is False
    assert result[0]["id"] == "ACN-high"
    assert result[0]["ranks"]["bm25"] == 1
