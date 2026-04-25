"""
FAQ store — loads Q&A pairs from knowledge/<lang>/ folders.

Supported formats:
- JSON: [{"q": "...", "a": "..."}, ...]
- Markdown: files with ## headers as questions, body as answer

Documents (for RAG) are loaded as plain text chunks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"


@dataclass
class FAQEntry:
    question: str
    answer: str
    source: str = ""
    page_ref: str = ""   # optional app page key (e.g. "import", "review", "rules")


@dataclass
class DocChunk:
    text: str
    source: str = ""
    lang: str = ""


def load_faq(lang: str = "it") -> list[FAQEntry]:
    """Load FAQ entries for a given language."""
    lang_dir = _KNOWLEDGE_DIR / lang
    if not lang_dir.is_dir():
        return []
    entries: list[FAQEntry] = []
    for path in sorted(lang_dir.iterdir()):
        if path.suffix == ".json":
            entries.extend(_load_json_faq(path))
        elif path.suffix == ".md":
            entries.extend(_load_md_faq(path))
    return entries


def load_documents(lang: str = "it", chunk_size: int = 500) -> list[DocChunk]:
    """Load document chunks for RAG from knowledge/<lang>/docs/."""
    docs_dir = _KNOWLEDGE_DIR / lang / "docs"
    if not docs_dir.is_dir():
        return []
    chunks: list[DocChunk] = []
    for path in sorted(docs_dir.rglob("*")):
        if path.suffix in (".md", ".txt"):
            text = path.read_text(encoding="utf-8")
            for chunk in _split_text(text, chunk_size):
                chunks.append(DocChunk(text=chunk, source=str(path.name), lang=lang))
    return chunks


# ── private ──────────────────────────────────────────────────────────


def _load_json_faq(path: Path) -> list[FAQEntry]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [
        FAQEntry(
            question=item["q"],
            answer=item["a"],
            source=path.name,
            page_ref=item.get("page", ""),
        )
        for item in data
        if "q" in item and "a" in item
    ]


def _load_md_faq(path: Path) -> list[FAQEntry]:
    """Parse markdown: ## heading = question, body until next ## = answer."""
    text = path.read_text(encoding="utf-8")
    entries: list[FAQEntry] = []
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts[1:]:  # skip content before first ##
        lines = part.strip().split("\n", 1)
        question = lines[0].strip()
        answer = lines[1].strip() if len(lines) > 1 else ""
        if question and answer:
            entries.append(FAQEntry(question=question, answer=answer, source=path.name))
    return entries


def _split_text(text: str, chunk_size: int) -> list[str]:
    """Split text into chunks by paragraphs, respecting chunk_size."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > chunk_size and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks
