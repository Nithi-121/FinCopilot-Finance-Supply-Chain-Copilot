"""Small, auditable orchestration layer for retrieval plus data-backed financial tools."""
from __future__ import annotations

import re
from typing import Any

from src.generate import generate_answer
from src.retrieve import retrieve_with_ranks
from src.tools import get_anomalies, get_financials, get_forecast

METRICS = ("operating cash flow", "gross margin", "operating margin", "operating income", "net income", "revenue", "gross profit", "cash flow", "earnings per share", "eps")
METRIC_ALIASES = {"cash flow": "operating cash flow", "earnings per share": "eps"}
COMPANY_ALIASES = {"HP": "HPE", "HEWLETT PACKARD ENTERPRISE": "HPE", "MICROSOFT": "MSFT", "APPLE": "AAPL", "NVIDIA": "NVDA", "ACCENTURE": "ACN"}
FILING_TERMS = re.compile(
    r"\b(10\s*[- ]?k|annual report|sec|filing|item\s*(?:1a?|7|8)|risk|disclos|management|"
    r"revenue|income|cash flow|profit|margin|earnings|eps|financial|forecast|predict|"
    r"anomal|outlier|business|strategy|segment|company facts|xbrl|HPE|MSFT|AAPL|NVDA|ACN|"
    r"microsoft|apple|nvidia|accenture|hewlett packard)\b",
    re.IGNORECASE,
)
SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|good morning|good afternoon|good evening|how are you|thanks|thank you)\b", re.IGNORECASE)
CONCEPT_QUESTION = re.compile(
    r"^\s*(what is|what are|what does .+ mean|what does .+ stand for|define|explain|how do i|how can i|how does)\b",
    re.IGNORECASE,
)
SOURCE_REQUEST = re.compile(r"\b(10\s*[- ]?k|annual report|sec filing|in (?:the|its|their) (?:filing|report)|according to (?:the|this))\b", re.IGNORECASE)


def route_tools(question: str) -> dict[str, Any]:
    q = question.lower()
    company_match = re.search(r"\b(Hewlett Packard Enterprise|HPE|HP|MSFT|Microsoft|AAPL|Apple|NVDA|Nvidia|ACN|Accenture)\b", question, re.IGNORECASE)
    company = COMPANY_ALIASES.get(company_match.group(1).upper(), company_match.group(1).upper()) if company_match else None
    metric = next((m for m in METRICS if m in q), None)
    metric = METRIC_ALIASES.get(metric, metric)
    if not company or not metric:
        return {}
    if any(w in q for w in ("forecast", "predict", "next quarter", "future")):
        return {"get_forecast": get_forecast(company, metric)}
    if "anomal" in q or "outlier" in q:
        return {"get_anomalies": get_anomalies(company, metric)}
    if any(w in q for w in ("how much", "what was", "compare", "figure", "number", "value", "margin", "revenue")):
        return {"get_financials": get_financials(company, metric)}
    return {}


def ask(
    question: str, company: str | None = None, year: str | None = None, top_k: int = 3,
    retrieval_mode: str = "bm25", compare_company: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    if company is not None:
        company = COMPANY_ALIASES.get(str(company).upper(), str(company).upper())
    else:
        name_match = re.search(r"\b(Hewlett Packard Enterprise|HPE|HP|MSFT|Microsoft|AAPL|Apple|NVDA|Nvidia|ACN|Accenture)\b", question, re.IGNORECASE)
        company = COMPANY_ALIASES.get(name_match.group(1).upper(), name_match.group(1).upper()) if name_match else None
    if retrieval_mode not in {"hybrid", "dense", "bm25"}:
        raise ValueError("retrieval_mode must be one of: hybrid, dense, bm25")
    history = conversation_history or []
    previous_context = " ".join(turn.get("content", "")[-350:] for turn in history[-4:])
    follow_up = bool(previous_context and re.search(r"\b(it|they|them|their|that|those|more|elaborate|why)\b", question, re.IGNORECASE))
    named_company = bool(re.search(r"\b(HPE|MSFT|AAPL|NVDA|ACN|Microsoft|Apple|NVIDIA|Accenture|Hewlett Packard)\b", question, re.IGNORECASE))
    concept_only = bool(CONCEPT_QUESTION.match(question)) and not named_company
    filing_question = bool(FILING_TERMS.search(question) or (follow_up and FILING_TERMS.search(previous_context)))
    conversational = bool(SMALL_TALK.match(question)) or concept_only or not filing_question
    retrieval_query = question
    if follow_up and FILING_TERMS.search(previous_context):
        retrieval_query = f"{previous_context[-500:]}\nFollow-up: {question}"

    def run_for(ticker: str | None) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        hits = [] if conversational else retrieve_with_ranks(
            retrieval_query, company=ticker, year=year, k=top_k, mode=retrieval_mode
        )
        tool_question = f"{ticker or ''} {question}".strip()
        try:
            results = route_tools(tool_question)
        except Exception as exc:  # noqa: BLE001 - include tool failures in the inspectable response
            results = {"tool_error": str(exc)}
        return hits, results, generate_answer(question, hits, results, history)

    sections, tool_results, answer = run_for(company)
    comparison = None
    if compare_company:
        compare_company = COMPANY_ALIASES.get(compare_company.upper(), compare_company.upper())
        if compare_company not in {"HPE", "MSFT", "AAPL", "NVDA", "ACN"} or compare_company == company:
            raise ValueError("compare_company must be a different supported ticker")
        other_sections, other_tools, other_answer = run_for(compare_company)
        answer = f"## {company or 'Selected company'}\n{answer}\n\n## {compare_company}\n{other_answer}"
        sections = sections + other_sections
        tool_results = {"first_company": tool_results, "second_company": other_tools}
        comparison = compare_company
    return {
        "answer": answer,
        "citations": [
            {"id": x["id"], "company": x["company"], "fiscal_year": x["fiscal_year"], "section": x["section"], "source_file": x["source_file"], "text": x["text"], "ranks": x.get("ranks")}
            for x in sections
        ],
        "tools_used": ["search_filings", *tool_results.keys()],
        "tool_results": tool_results,
        "retrieval_mode": retrieval_mode,
        "comparison_company": comparison,
        "answer_type": "conversational" if conversational else "filing_research",
    }
