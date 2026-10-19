"""Convert SEC HTML into section-aware, traceable text chunks."""
from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

FILINGS = Path("data/filings")
OUT = Path("data/chunks/chunks.jsonl")
SECTION_PATTERNS = [
    ("Item 1 Business", r"item\s*1\.?\s+business\b"),
    ("Item 1A Risk Factors", r"item\s*1a\.?\s+risk factors\b"),
    ("Item 7 MD&A", r"item\s*7\.?\s+management.{0,100}results of operations"),
    ("Item 8 Financial Statements", r"item\s*8\.?\s+(?:notes to )?financial.{0,100}statements(?:.{0,40}supplementary data)?"),
]


def html_to_text(path: Path) -> str:
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    for node in soup(["script", "style", "ix:header", "ix:hidden"]):
        node.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Some inline SEC filing layouts split a displayed heading word across spans
    # (for example, "B USINESS" or "RIS K"). Repair only these known Item labels.
    for broken, whole in ((r"B\s+USINESS", "BUSINESS"), (r"RIS\s+K", "RISK"),
                          (r"FINANCI\s+AL", "FINANCIAL"), (r"STATEMENT\s+S", "STATEMENTS")):
        text = re.sub(broken, whole, text, flags=re.IGNORECASE)
    return text


def split_sections(text: str) -> list[tuple[str, str]]:
    matches = []
    for name, pattern in SECTION_PATTERNS:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            following = text[match.end() : match.end() + 35]
            # SEC filings repeat the active Item heading in the page header and
            # include page numbers in the table of contents. Neither is a section start.
            if re.match(r"\s+\d{1,3}(?:\s|$)", following):
                continue
            matches.append((match.start(), name))
    matches.sort()
    if not matches:
        return [("Unsectioned filing", text)]
    # Select the first real body heading for each item. Page headers are filtered
    # above, so later repeated headings do not shift section boundaries.
    first_match: dict[str, int] = {}
    for start, name in matches:
        first_match.setdefault(name, start)
    chosen = sorted((start, name) for name, start in first_match.items())
    sections = []
    for i, (start, name) in enumerate(chosen):
        end = chosen[i + 1][0] if i + 1 < len(chosen) else len(text)
        block = text[start:end].strip()
        if len(block) > 500:
            sections.append((name, block))
    if not sections:
        return [("Unsectioned filing", text)]
    return sections


def chunk_text(text: str, size: int = 650, overlap: int = 100) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [" ".join(words[i : i + size]) for i in range(0, len(words), step)]


def parse_file(path: Path) -> list[dict[str, object]]:
    ticker, report_date = path.name.split("_", 2)[:2]
    fiscal_year = report_date[:4]
    text = html_to_text(path)
    chunks = []
    for section, content in split_sections(text):
        for part, body in enumerate(chunk_text(content)):
            record = {
                "id": f"{ticker}-{fiscal_year}-{section.lower().replace(' ', '-')}-{len(chunks):04d}",
                "company": ticker,
                "fiscal_year": fiscal_year,
                "section": section,
                "source_file": path.name,
                "part": part,
                "text": body,
            }
            chunks.append(record)
    return chunks


def parse_all(files: list[Path] | None = None) -> list[dict[str, object]]:
    paths = files if files is not None else sorted(FILINGS.glob("*.html"))
    if not paths:
        raise FileNotFoundError("No filings found. Run `python -m src.fetch` first.")
    all_chunks = [chunk for path in paths for chunk in parse_file(path)]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in all_chunks), encoding="utf-8")
    return all_chunks


if __name__ == "__main__":
    records = parse_all()
    print(f"Created {len(records)} chunks in {OUT}")
