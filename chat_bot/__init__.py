"""
Spendif.ai ChatBot — Adaptive help/support chatbot.

Three modes, auto-selected based on available resources:
- RAG Cloud:  external API enabled (Claude/OpenAI) + vector retrieval
- RAG Local:  Ollama available + vector retrieval
- FAQ Match:  deterministic TF-IDF classifier, no LLM required
"""

from chat_bot.engine import ChatBotEngine, ChatMode

__all__ = ["ChatBotEngine", "ChatMode"]
