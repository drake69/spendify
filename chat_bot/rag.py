"""
RAG engine — retrieval-augmented generation for the chatbot.

Uses TF-IDF for retrieval (zero external deps) and delegates generation
to an LLM backend (local Ollama or cloud API via Spendify's BackendFactory).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from chat_bot.faq_store import DocChunk, FAQEntry

_PROMPTS_PATH = Path(__file__).parent / "prompts.json"


@dataclass
class RAGResult:
    answer: str
    sources: list[str]
    context_used: str


class RAGEngine:
    """Retrieval-Augmented Generation over FAQ + document chunks."""

    def __init__(
        self,
        faq_entries: list[FAQEntry],
        doc_chunks: list[DocChunk],
        llm_backend,  # core.llm_backends.LLMBackend
        top_k: int = 5,
        lang: str = "it",
    ):
        self._llm = llm_backend
        self._top_k = top_k
        self._lang = lang
        self._prompts = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))

        # build unified corpus: FAQ questions+answers + doc chunks
        self._corpus: list[_CorpusEntry] = []
        for entry in faq_entries:
            self._corpus.append(_CorpusEntry(
                text=f"{entry.question}\n{entry.answer}",
                source=entry.source,
                is_faq=True,
                faq_answer=entry.answer,
            ))
        for chunk in doc_chunks:
            self._corpus.append(_CorpusEntry(
                text=chunk.text,
                source=chunk.source,
                is_faq=False,
            ))

        # build TF-IDF index
        self._idf: dict[str, float] = {}
        self._tfidf_matrix: list[dict[str, float]] = []
        self._build_index()

    def query(self, question: str) -> RAGResult:
        """Retrieve relevant context and generate an answer via LLM."""
        # retrieve top-k chunks
        q_vec = self._tfidf_vector(self._tokenize(question))
        scored = []
        for i, doc_vec in enumerate(self._tfidf_matrix):
            score = self._cosine_similarity(q_vec, doc_vec)
            scored.append((score, i))
        scored.sort(reverse=True)
        top_entries = [self._corpus[i] for _, i in scored[: self._top_k]]

        context = "\n\n---\n\n".join(e.text for e in top_entries)
        sources = list({e.source for e in top_entries if e.source})

        # generate answer via LLM
        system_key = f"system_rag_{self._lang}" if f"system_rag_{self._lang}" in self._prompts else "system_rag"
        system_prompt = self._prompts[system_key]
        user_prompt = self._prompts["user_template"].format(
            context=context, question=question,
        )

        answer = self._call_llm(system_prompt, user_prompt)
        return RAGResult(answer=answer, sources=sources, context_used=context)

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM backend. Uses complete_structured with a simple text schema."""
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
        }
        try:
            result = self._llm.complete_structured(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=schema,
                temperature=0.1,
            )
            if result and "answer" in result:
                return result["answer"]
        except Exception:
            pass
        # fallback: return no-answer message
        no_answer = self._prompts.get("no_answer", {})
        return no_answer.get(self._lang, no_answer.get("en", "No answer available."))

    # ── TF-IDF index (same approach as faq_classifier) ───────────────

    def _build_index(self) -> None:
        tokenized = [self._tokenize(e.text) for e in self._corpus]
        doc_count = len(tokenized)
        df: Counter[str] = Counter()
        for tokens in tokenized:
            df.update(set(tokens))
        self._idf = {
            term: math.log((doc_count + 1) / (freq + 1)) + 1
            for term, freq in df.items()
        }
        self._tfidf_matrix = [self._tfidf_vector(tokens) for tokens in tokenized]

    def _tfidf_vector(self, tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        total = len(tokens) if tokens else 1
        return {
            term: (count / total) * self._idf.get(term, 1.0)
            for term, count in tf.items()
        }

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


@dataclass
class _CorpusEntry:
    text: str
    source: str = ""
    is_faq: bool = False
    faq_answer: str = ""
