"""LLM service — facade for LLM backend operations used by the UI.

Keeps the coupling gate clean: UI files import only from services.*,
never from core.llm_backends or core.model_manager directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


# ── Context-length detection ─────────────────────────────────────────────────

def detect_llama_cpp_context(model_path: str = "") -> int | None:
    """Read GGUF context length. If *model_path* is empty, uses the default."""
    from core.llm_backends import LlamaCppBackend
    if not model_path:
        try:
            model_path = LlamaCppBackend._default_model_path()
        except Exception:
            return None
    return LlamaCppBackend.read_gguf_context_length(model_path)


def detect_ollama_context(model: str, base_url: str = "http://localhost:11434") -> int | None:
    """Query Ollama /api/show for the model's context length."""
    from core.llm_backends import OllamaBackend
    return OllamaBackend.fetch_context_length(model, base_url)


def get_known_context_window(model: str) -> int | None:
    """Lookup a known context window for OpenAI / Claude models."""
    from core.llm_backends import _KNOWN_CONTEXT
    return _KNOWN_CONTEXT.get(model)


def detect_vllm_context(base_url: str, model: str) -> int | None:
    """Query vLLM /v1/models for the model's context length."""
    from core.llm_backends import VllmBackend
    return VllmBackend.fetch_context_length(base_url, model)


# ── LLM test / validation ───────────────────────────────────────────────────

def test_llm_backend(
    backend: str,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    **extra_kwargs: Any,
) -> tuple[bool, str]:
    """Send a minimal test prompt. Returns (success, message)."""
    from core.llm_backends import BackendFactory, LLMValidationError

    try:
        kwargs: dict = {"timeout": 15}
        if backend == "local_llama_cpp":
            kwargs.pop("timeout", None)
            kwargs.update(extra_kwargs)
        elif backend == "local_ollama":
            kwargs["base_url"] = base_url
            kwargs["model"] = model
        elif backend == "openai":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend == "claude":
            kwargs["api_key"] = api_key
            kwargs["model"] = model
        elif backend in ("vllm", "vllm_offline"):
            kwargs["base_url"] = base_url
            kwargs["model"] = model
            kwargs["api_key"] = api_key or "none"

        be = BackendFactory.create(backend, **kwargs)
        resp = be.complete_structured(
            system_prompt="You are a test assistant.",
            user_prompt="Reply with exactly: OK",
            json_schema={"type": "object", "properties": {"reply": {"type": "string"}}},
        )
        return True, str(resp)
    except LLMValidationError as exc:
        return False, f"Validation: {exc}"
    except Exception as exc:
        return False, str(exc)


# ── Local model listing ──────────────────────────────────────────────────────

def list_local_llama_cpp_models() -> list[dict[str, Any]]:
    """List GGUF models in ~/.spendifai/models/ and the default model dir."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.list_local_models()


def get_default_gguf_models() -> dict:
    """Return the DEFAULT_GGUF_MODELS dict for the download UI."""
    from core.llm_backends import DEFAULT_GGUF_MODELS
    return DEFAULT_GGUF_MODELS


def get_llama_cpp_default_model_path() -> str:
    """Return the default model path for llama.cpp."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend._default_model_path()


def read_gguf_context_length(path: str) -> int | None:
    """Read context length from a GGUF file's metadata."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.read_gguf_context_length(path)


def download_gguf_model(url: str, dest: str, progress_callback=None) -> str:
    """Download a GGUF model file. Returns the destination path."""
    from core.llm_backends import LlamaCppBackend
    return LlamaCppBackend.download_model(url, dest, progress_callback)


# ── Backend factory ──────────────────────────────────────────────────────────

def create_backend(backend_name: str, **kwargs):
    """Create an LLM backend instance via BackendFactory."""
    from core.llm_backends import BackendFactory
    return BackendFactory.create(backend_name, **kwargs)


# Re-export LLMValidationError so UI can catch it without importing core
def _get_validation_error_class():
    from core.llm_backends import LLMValidationError
    return LLMValidationError

# Lazy re-export: importable as `from services.llm_service import LLMValidationError`
try:
    from core.llm_backends import LLMValidationError
except ImportError:
    pass


# ── Hardware detection + model recommendation ────────────────────────────────

def detect_system_hardware() -> dict[str, Any]:
    """Detect OS, RAM, GPU, VRAM."""
    from core.model_manager import detect_hw
    return detect_hw()


def list_available_models() -> list[Path]:
    """List GGUF files in ~/.spendifai/models/."""
    from core.model_manager import list_local_models
    return list_local_models()


def get_recommended_model(ram_gb: int):
    """Return the best ModelInfo for the given RAM, or None."""
    from config import get_recommended_model as _get_rec
    return _get_rec(ram_gb)


# ── History engine ───────────────────────────────────────────────────────────

def get_description_profiles(engine) -> list:
    """Fetch description profiles (for analytics associations chart)."""
    from core.history_engine import get_description_profiles as _get_profiles
    from db.models import get_session
    with get_session(engine) as session:
        return _get_profiles(session)
