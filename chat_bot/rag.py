"""
RAG engine — retrieval-augmented generation for the chatbot.

Uses TF-IDF for retrieval (zero external deps) and delegates generation
to an LLM backend (local Ollama or cloud API via Spendif.ai's BackendFactory).

Query pipeline:
  1. _extract_key_terms  — LLM strips function words from the query (lang-agnostic)
  2. TF-IDF retrieval    — cosine similarity on the cleaned query
  3. _call_llm           — answer generation with retrieved context + history
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

from chat_bot.kb_store import DocChunk, FAQEntry

_PROMPTS_PATH = Path(__file__).parent / "prompts.json"
_MAX_HISTORY_TURNS = 3  # number of past Q&A pairs to include in the prompt
_MIN_SIMILARITY = 0.05  # below this, corpus has no relevant content → skip generation


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
                source=entry.page_ref or "",   # page_ref preferred over filename
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

    def query(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> RAGResult:
        """Retrieve relevant context and generate an answer via LLM.

        Args:
            question: The current user question.
            history: Optional list of previous turns, each
                     ``{"role": "user"|"assistant", "content": "..."}``.
                     The last ``_MAX_HISTORY_TURNS`` turns are included in
                     the prompt so the model can resolve follow-up questions.
        """
        # ── Step 1: analyze query — intent + key terms (lang-agnostic via LLM) ──
        terms = self._analyze_query(question)
        logger.info("RAG query analysis: question=%r terms=%r", question, terms)

        if not terms:
            # Treat as greeting only when the message is clearly just a salutation:
            # no question mark and short enough to not contain a hidden question.
            # If the message is longer or contains '?', the extractor likely missed
            # the question part — fall back to the raw question for retrieval.
            if "?" not in question and len(question.strip()) <= 30:
                system_prompt = self._prompts.get("system_greeting", "You are a helpful support assistant.")
                answer = self._call_llm(system_prompt, question)
                return RAGResult(answer=answer, sources=[], context_used="")
            terms = question  # retrieval on raw question as fallback
        retrieval_query = terms

        # ── Step 2: TF-IDF retrieval ─────────────────────────────────────────
        def _retrieve(query: str):
            q_vec = self._tfidf_vector(self._tokenize(query))
            return sorted(
                ((self._cosine_similarity(q_vec, dv), i) for i, dv in enumerate(self._tfidf_matrix)),
                reverse=True,
            )

        scored = _retrieve(retrieval_query)
        best_score = scored[0][0] if scored else 0.0

        # Always compare against the raw question: if a different rewrite was used and
        # the raw query finds a better top-1, prefer it.  This prevents a bad rewrite
        # (e.g. small models adding off-topic synonyms) from steering retrieval away
        # from documents that the raw question would have found directly.
        if retrieval_query != question:
            scored_raw = _retrieve(question)
            best_raw = scored_raw[0][0] if scored_raw else 0.0
            if best_raw > best_score:
                scored = scored_raw
                best_score = best_raw
                logger.info("RAG rewrite fallback: raw=%.3f > rewritten=%.3f", best_raw, scored[0][0] if scored else 0.0)
            else:
                logger.info("RAG rewrite kept: rewritten=%.3f >= raw=%.3f", best_score, best_raw)

        top3 = [(f"{sc:.3f}", self._corpus[i].source[:40]) for sc, i in scored[:3]]
        logger.info("RAG top score=%.3f query=%r top3=%s", best_score, retrieval_query, top3)

        # If the best match is below threshold, the corpus has no relevant content.
        # Skip generation and return a language-aware no-answer directly.
        if best_score < _MIN_SIMILARITY:
            no_answer = self._prompts.get("no_answer", {})
            answer = no_answer.get(self._lang, no_answer.get("en", "I don't have enough information to answer."))
            return RAGResult(answer=answer, sources=[], context_used="")

        top_entries = [self._corpus[i] for _, i in scored[: self._top_k]]

        context = "\n\n---\n\n".join(e.text for e in top_entries)
        sources = list({e.source for e in top_entries if e.source})

        # ── Step 3: build history snippet ────────────────────────────────────
        history_text = ""
        if history:
            recent = history[-_MAX_HISTORY_TURNS * 2:]
            lines = []
            for msg in recent:
                role = "Utente" if msg["role"] == "user" else "Assistente"
                lines.append(f"{role}: {msg['content']}")
            if lines:
                history_text = "\n".join(lines)

        # ── Step 4: generate answer ───────────────────────────────────────────
        system_key = f"system_rag_{self._lang}" if f"system_rag_{self._lang}" in self._prompts else "system_rag"
        system_prompt = self._prompts[system_key]
        user_prompt_template = (
            self._prompts.get("user_template_with_history")
            if history_text else None
        ) or self._prompts["user_template"]
        user_prompt = user_prompt_template.format(
            context=context,
            question=question,
            history=history_text,
        )

        answer = self._call_llm(system_prompt, user_prompt)
        return RAGResult(answer=answer, sources=sources, context_used=context)

    # ── query analysis (Step 1) ───────────────────────────────────────────────

    def _analyze_query(self, question: str) -> str:
        """Rewrite the user question into an expanded search query via LLM.

        Returns a richer version of the question with synonyms and related
        terms that improve TF-IDF retrieval, in the same language as the
        original. Returns an empty string if the message is a pure greeting
        with no support question. Falls back to the raw question on failure.
        """
        system_prompt = self._prompts.get("rewrite_query_system")
        if not system_prompt:
            return question

        user_template = self._prompts.get("rewrite_query_user")
        if not user_template:
            return question

        user_prompt = user_template.format(question=question)
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        system_with_format = (
            system_prompt
            + '\n\nRespond ONLY with a JSON object: {"query": "<rewritten question, or empty string if pure greeting>"}'
        )
        try:
            result = self._llm.complete_structured(
                system_prompt=system_with_format,
                user_prompt=user_prompt,
                json_schema=schema,
                temperature=0.0,
            )
            return (result or {}).get("query", "").strip()
        except Exception as exc:
            logger.warning("_analyze_query failed (%s: %s) — using raw question",
                           type(exc).__name__, exc)
        return question

    # ── answer generation (Step 4) ────────────────────────────────────────────

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call the LLM backend. Uses complete_structured with a simple text schema."""
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        system_with_format = (
            system_prompt
            + '\n\nRespond ONLY with a JSON object: {"answer": "<your reply here>"}'
        )
        try:
            result = self._llm.complete_structured(
                system_prompt=system_with_format,
                user_prompt=user_prompt,
                json_schema=schema,
                temperature=0.1,
            )
            if result and "answer" in result:
                return result["answer"]
            logger.warning("RAG _call_llm: result missing 'answer' key: %r", result)
        except Exception as exc:
            logger.warning("RAG _call_llm failed (%s: %s) — falling back to no-answer",
                           type(exc).__name__, exc)
        # Last-resort fallback: ask the model to apologise in the user's language.
        # The user_prompt contains the original question, so the model can infer the language.
        lang_guard = self._prompts.get("_lang_guard", "")
        fallback_system = f"You are a support assistant. {lang_guard}"
        fallback_user = f"Tell the user briefly that you cannot answer their question and suggest contacting support. Their message was: {user_prompt}"
        try:
            result = self._llm.complete_structured(
                system_prompt=fallback_system + '\n\nRespond ONLY with a JSON object: {"answer": "<your reply>"}',
                user_prompt=fallback_user,
                json_schema=schema,
                temperature=0.0,
            )
            if result and "answer" in result:
                return result["answer"]
        except Exception:
            pass
        no_answer = self._prompts.get("no_answer", {})
        return no_answer.get(self._lang, no_answer.get("en", "No answer available."))

    # ── TF-IDF index ──────────────────────────────────────────────────────────

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
