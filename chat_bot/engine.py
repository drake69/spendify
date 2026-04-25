"""
ChatBotEngine — adaptive chatbot that auto-selects the best mode.

Priority order:
1. RAG Cloud  — if external API is configured (Claude/OpenAI)
2. RAG Local  — if Ollama is reachable
3. FAQ Match  — deterministic fallback, no LLM needed
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from chat_bot.faq_classifier import ClassifierResult, FAQClassifier
from chat_bot.kb_store import load_documents, load_faq
from chat_bot.rag import RAGEngine, RAGResult

logger = logging.getLogger(__name__)

_PROMPTS_PATH = Path(__file__).parent / "prompts.json"


class ChatMode(str, Enum):
    RAG_CLOUD = "rag_cloud"
    RAG_LOCAL = "rag_local"
    FAQ_MATCH = "faq_match"


@dataclass
class ChatResponse:
    text: str
    mode: ChatMode
    confidence: float | None = None
    sources: list[str] | None = None


class ChatBotEngine:
    """
    Adaptive chatbot engine.

    Usage:
        engine = ChatBotEngine(db_engine, lang="it")
        response = engine.ask("Come importo un file?")
    """

    def __init__(self, db_engine=None, lang: str = "it"):
        self._db_engine = db_engine
        self._lang = lang
        self._prompts = json.loads(_PROMPTS_PATH.read_text(encoding="utf-8"))

        # load knowledge base — all available languages so any query language works
        self._faq_entries, self._doc_chunks = _load_all_knowledge()

        # always build FAQ classifier (zero cost)
        self._classifier = FAQClassifier(self._faq_entries) if self._faq_entries else None

        # detect available mode
        self._mode = self._detect_mode()
        self._rag: RAGEngine | None = None

        logger.info("ChatBot initialized — mode=%s, faq=%d, docs=%d",
                     self._mode.value, len(self._faq_entries), len(self._doc_chunks))

    @property
    def mode(self) -> ChatMode:
        return self._mode

    def ask(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        """Answer a user question using the best available mode.

        Args:
            question: The current user question.
            history: Optional list of previous turns as
                     ``[{"role": "user"|"assistant", "content": "..."}]``.
                     Passed to RAG so the model can resolve follow-up questions.
        """
        question = question.strip()
        if not question:
            return ChatResponse(text=self._no_answer(), mode=self._mode)

        if self._mode in (ChatMode.RAG_CLOUD, ChatMode.RAG_LOCAL):
            return self._ask_rag(question, history=history)
        return self._ask_faq(question)

    # ── mode detection ───────────────────────────────────────────────

    def _detect_mode(self) -> ChatMode:
        """Detect chatbot mode from the user's LLM settings.

        The mode follows whatever the user configured in Settings:
        - Cloud API (openai, claude, openai_compatible) → RAG Cloud
        - Local backend (local_ollama, vllm) → RAG Local
        - Local non-server backend (local_llama_cpp) or none → FAQ Match
        """
        if self._db_engine is None:
            return ChatMode.FAQ_MATCH
        try:
            settings = self._read_settings()
            backend = settings.get("llm_backend", "")

            # cloud backends: need a valid API key
            if backend in ("openai", "claude", "openai_compatible"):
                key = (
                    settings.get("openai_api_key")
                    or settings.get("anthropic_api_key")
                    or settings.get("openai_compatible_api_key")
                )
                if key:
                    return ChatMode.RAG_CLOUD

            # local server backends: Ollama, vLLM
            if backend in ("local_ollama", "vllm"):
                return ChatMode.RAG_LOCAL

        except Exception:
            pass
        # local_llama_cpp or unconfigured → deterministic FAQ
        return ChatMode.FAQ_MATCH

    def _read_settings(self) -> dict[str, str]:
        """Read user settings from DB."""
        from db.repository import get_all_user_settings
        from sqlalchemy.orm import Session
        with Session(self._db_engine) as session:
            return get_all_user_settings(session)

    # ── ask implementations ──────────────────────────────────────────

    def _ask_rag(
        self,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> ChatResponse:
        """Answer using RAG (cloud or local)."""
        if self._rag is None:
            self._rag = self._build_rag()
        if self._rag is None:
            # fallback to FAQ if backend construction fails
            return self._ask_faq(question)
        result: RAGResult = self._rag.query(question, history=history)
        return ChatResponse(
            text=result.answer,
            mode=self._mode,
            sources=result.sources,
        )

    def _ask_faq(self, question: str) -> ChatResponse:
        """Answer using deterministic FAQ matching."""
        if self._classifier is None:
            return ChatResponse(text=self._no_answer(), mode=ChatMode.FAQ_MATCH)
        result: ClassifierResult | None = self._classifier.classify(question)
        if result is None:
            return ChatResponse(text=self._no_answer(), mode=ChatMode.FAQ_MATCH, confidence=0.0)
        return ChatResponse(
            text=result.answer,
            mode=ChatMode.FAQ_MATCH,
            confidence=result.confidence,
            sources=[result.source] if result.source else None,
        )

    # ── backend construction ─────────────────────────────────────────

    def _build_rag(self) -> RAGEngine | None:
        """Build RAG engine with the appropriate LLM backend."""
        try:
            from core.llm_backends import BackendFactory
            if self._mode == ChatMode.RAG_CLOUD:
                backend = self._build_cloud_backend()
            else:
                backend = BackendFactory.create("local_ollama")
            if backend is None:
                return None
            return RAGEngine(
                faq_entries=self._faq_entries,
                doc_chunks=self._doc_chunks,
                llm_backend=backend,
                lang=self._lang,
            )
        except Exception as e:
            logger.warning("Failed to build RAG engine: %s", e)
            return None

    def _build_cloud_backend(self):
        """Build a cloud LLM backend from user settings."""
        from core.llm_backends import BackendFactory
        from db.repository import get_all_user_settings
        from sqlalchemy.orm import Session
        with Session(self._db_engine) as session:
            settings = get_all_user_settings(session)
        backend_name = settings.get("llm_backend", "openai")
        kwargs = {}
        if backend_name == "openai":
            kwargs["api_key"] = settings.get("openai_api_key", "")
            kwargs["model"] = settings.get("openai_model", "gpt-4o-mini")
        elif backend_name == "claude":
            kwargs["api_key"] = settings.get("anthropic_api_key", "")
            kwargs["model"] = settings.get("claude_model", "claude-3-5-haiku-20241022")
        elif backend_name == "openai_compatible":
            kwargs["api_key"] = settings.get("openai_compatible_api_key", "")
            kwargs["base_url"] = settings.get("openai_compatible_base_url", "")
            kwargs["model"] = settings.get("openai_compatible_model", "")
        return BackendFactory.create(backend_name, **kwargs)

    # ── helpers ──────────────────────────────────────────────────────

    def _no_answer(self) -> str:
        no_answer = self._prompts.get("no_answer", {})
        return no_answer.get(self._lang, no_answer.get("en", "No answer available."))


def _load_all_knowledge() -> tuple[list, list]:
    """Load FAQ entries and doc chunks from all available language directories."""
    from pathlib import Path
    knowledge_dir = Path(__file__).parent / "knowledge"
    faq_entries: list = []
    doc_chunks: list = []
    for lang_dir in sorted(knowledge_dir.iterdir()):
        if lang_dir.is_dir():
            faq_entries.extend(load_faq(lang_dir.name))
            doc_chunks.extend(load_documents(lang_dir.name))
    return faq_entries, doc_chunks
