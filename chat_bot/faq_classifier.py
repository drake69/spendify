"""
FAQ Classifier — deterministic mode (no LLM required).

Uses TF-IDF + cosine similarity to match user questions to known FAQ entries.
Returns the best matching FAQ answer if similarity exceeds the threshold.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from chat_bot.faq_store import FAQEntry


@dataclass
class ClassifierResult:
    answer: str
    confidence: float
    matched_question: str
    source: str


class FAQClassifier:
    """TF-IDF based FAQ matcher — zero dependencies, works on any hardware."""

    def __init__(self, faq_entries: list[FAQEntry], threshold: float = 0.3):
        self._entries = faq_entries
        self._threshold = threshold
        self._vocab: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._tfidf_matrix: list[dict[str, float]] = []
        self._build_index()

    def classify(self, question: str) -> ClassifierResult | None:
        """Find the best matching FAQ for a question. Returns None if below threshold."""
        if not self._entries:
            return None
        q_vec = self._tfidf_vector(self._tokenize(question))
        best_score = 0.0
        best_idx = 0
        for i, doc_vec in enumerate(self._tfidf_matrix):
            score = self._cosine_similarity(q_vec, doc_vec)
            if score > best_score:
                best_score = score
                best_idx = i
        if best_score < self._threshold:
            return None
        entry = self._entries[best_idx]
        return ClassifierResult(
            answer=entry.answer,
            confidence=best_score,
            matched_question=entry.question,
            source=entry.source,
        )

    # ── index building ───────────────────────────────────────────────

    def _build_index(self) -> None:
        tokenized = [self._tokenize(e.question) for e in self._entries]
        # build vocabulary and IDF
        doc_count = len(tokenized)
        df: Counter[str] = Counter()
        for tokens in tokenized:
            df.update(set(tokens))
        self._idf = {
            term: math.log((doc_count + 1) / (freq + 1)) + 1
            for term, freq in df.items()
        }
        # build TF-IDF vectors
        self._tfidf_matrix = [self._tfidf_vector(tokens) for tokens in tokenized]

    def _tfidf_vector(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        return {
            term: (count / total) * self._idf.get(term, 1.0)
            for term, count in tf.items()
        }

    # ── utilities ────────────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        return [w for w in text.split() if len(w) > 1]

    @staticmethod
    def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
        common = set(a) & set(b)
        if not common:
            return 0.0
        dot = sum(a[k] * b[k] for k in common)
        norm_a = math.sqrt(sum(v * v for v in a.values()))
        norm_b = math.sqrt(sum(v * v for v in b.values()))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
