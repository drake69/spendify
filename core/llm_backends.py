from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from support.logging import setup_logging

logger = setup_logging()


class LLMValidationError(Exception):
    pass


class SanitizationRequiredError(Exception):
    pass


class LLMBackend(ABC):

    @abstractmethod
    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Return a dict validated against json_schema.
        Raises LLMValidationError if the output is not conformant."""
        ...

    @property
    @abstractmethod
    def is_remote(self) -> bool:
        """True if the backend transmits data externally."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ── Ollama ────────────────────────────────────────────────────────────────────

class OllamaBackend(LLMBackend):
    """Local Ollama backend using the native /api/generate endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 60,
    ):
        import requests as _requests
        self._requests = _requests
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma3:12b")
        self.timeout = timeout

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "local_ollama"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "format": json_schema,
            "options": {"temperature": temperature},
        }
        try:
            resp = self._requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            content = resp.json()["response"]
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._requests.RequestException, KeyError, json.JSONDecodeError) as exc:
            raise LLMValidationError(f"OllamaBackend error: {exc}") from exc

    def is_available(self) -> bool:
        try:
            resp = self._requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False


# ── OpenAI ────────────────────────────────────────────────────────────────────

class OpenAIBackend(LLMBackend):

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 30):
        import openai as _openai
        self._openai = _openai
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        self.timeout = timeout

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "openai"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        client = self._openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": json_schema, "strict": True},
                },
                temperature=temperature,
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._openai.OpenAIError, json.JSONDecodeError, KeyError) as exc:
            raise LLMValidationError(f"OpenAIBackend error: {exc}") from exc


# ── Claude ────────────────────────────────────────────────────────────────────

class ClaudeBackend(LLMBackend):

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 30):
        import anthropic as _anthropic
        self._anthropic = _anthropic
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("CLAUDE_MODEL", "claude-3-5-haiku-20241022")
        self.timeout = timeout

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "claude"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        client = self._anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)
        tool_def = {
            "name": "submit_result",
            "description": "Submit the structured result.",
            "input_schema": json_schema,
        }
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[tool_def],
                tool_choice={"type": "tool", "name": "submit_result"},
                temperature=temperature,
            )
            tool_use = next(
                (b for b in response.content if b.type == "tool_use"),
                None,
            )
            if tool_use is None:
                raise LLMValidationError("Claude did not return a tool_use block")
            result = tool_use.input
            _validate_required(result, json_schema)
            return result
        except self._anthropic.APIError as exc:
            raise LLMValidationError(f"ClaudeBackend error: {exc}") from exc


# ── OpenAI-compatible (Groq, Together AI, Google AI Studio, …) ───────────────

class OpenAICompatibleBackend(LLMBackend):
    """Generic OpenAI-compatible backend for any provider that exposes /v1/chat/completions.

    Uses json_object response format (broadly supported) instead of strict json_schema,
    then validates required fields manually.  Works with Groq, Together AI,
    Google AI Studio (Gemma), and any other OpenAI-compatible API.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 60,
    ):
        import openai as _openai
        self._openai = _openai
        self.base_url = base_url.rstrip("/")
        self.api_key  = api_key
        self.model    = model
        self.timeout  = timeout

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "openai_compatible"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        client = self._openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        # Append JSON instruction to system prompt for providers that don't support
        # response_format=json_schema
        system_with_json = system_prompt + "\n\nRespond ONLY with valid JSON."
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_with_json},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._openai.OpenAIError, json.JSONDecodeError, KeyError) as exc:
            raise LLMValidationError(f"OpenAICompatibleBackend error: {exc}") from exc


# ── llama.cpp (local, no external service) ────────────────────────────────────

# Suggested GGUF models for the download UI
DEFAULT_GGUF_MODELS = {
    "gemma-2-2b-it-Q4_K_M": {
        "url": "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
        "size_gb": 1.6,
        "description": "Google Gemma 2B — leggero, buono per categorizzazione",
    },
    "phi-3-mini-4k-instruct-Q4_K_M": {
        "url": "https://huggingface.co/bartowski/Phi-3-mini-4k-instruct-GGUF/resolve/main/Phi-3-mini-4k-instruct-Q4_K_M.gguf",
        "size_gb": 2.3,
        "description": "Microsoft Phi-3 Mini — bilanciato qualità/velocità",
    },
}


class LlamaCppBackend(LLMBackend):
    """Local LLM backend using llama-cpp-python. No external service needed."""

    def __init__(
        self,
        model_path: str | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = -1,
        timeout: int = 120,
    ):
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "llama-cpp-python non è installato. "
                "Installa con: pip install llama-cpp-python"
            )
        if model_path is None:
            model_path = self._default_model_path()
        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"Modello non trovato in {model_path}. "
                f"Scarica un modello GGUF dalla pagina Impostazioni (Scarica modello)."
            )
        self._model_path = model_path
        self._llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

    @staticmethod
    def _default_model_path() -> str:
        """Default model storage location (~/.spendify/models/)."""
        models_dir = Path.home() / ".spendify" / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        # Look for any .gguf file, pick the first alphabetically
        gguf_files = sorted(models_dir.glob("*.gguf"))
        if gguf_files:
            return str(gguf_files[0])
        return str(models_dir / "model.gguf")

    @staticmethod
    def download_model(url: str, dest: str | None = None, progress_callback=None) -> str:
        """Download a GGUF model from a URL (e.g., HuggingFace).

        Args:
            url: Direct download URL for the .gguf file.
            dest: Destination path. Defaults to ~/.spendify/models/<filename>.
            progress_callback: Optional callable(bytes_downloaded, total_bytes).

        Returns:
            The path where the model was saved.
        """
        import urllib.request

        if dest is None:
            filename = url.rsplit("/", 1)[-1]
            dest = str(Path.home() / ".spendify" / "models" / filename)
        Path(dest).parent.mkdir(parents=True, exist_ok=True)

        if progress_callback is not None:
            def _reporthook(block_num, block_size, total_size):
                progress_callback(block_num * block_size, total_size)
            urllib.request.urlretrieve(url, dest, reporthook=_reporthook)
        else:
            urllib.request.urlretrieve(url, dest)
        return dest

    @staticmethod
    def list_local_models() -> list[dict]:
        """Return metadata for all .gguf files in the default models directory."""
        models_dir = Path.home() / ".spendify" / "models"
        if not models_dir.exists():
            return []
        results = []
        for p in sorted(models_dir.glob("*.gguf")):
            results.append({
                "name": p.stem,
                "path": str(p),
                "size_gb": round(p.stat().st_size / (1024**3), 2),
            })
        return results

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "local_llama_cpp"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        system_with_json = system_prompt + "\n\nRespond ONLY with valid JSON."
        try:
            # Try with system role first; fall back to merged prompt if unsupported
            messages = [
                {"role": "system", "content": system_with_json},
                {"role": "user", "content": user_prompt},
            ]
            try:
                response = self._llm.create_chat_completion(
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                )
            except Exception as e:
                if "System role not supported" in str(e) or "system" in str(e).lower():
                    # Model doesn't support system role — merge into user prompt
                    merged = f"{system_with_json}\n\n---\n\n{user_prompt}"
                    response = self._llm.create_chat_completion(
                        messages=[{"role": "user", "content": merged}],
                        response_format={"type": "json_object"},
                        temperature=temperature,
                    )
                else:
                    raise
            raw = response["choices"][0]["message"]["content"]
            result = json.loads(raw)
            _validate_required(result, json_schema)
            return result
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise LLMValidationError(f"LlamaCppBackend error: {exc}") from exc
        except Exception as exc:
            # Catch llama_cpp runtime errors (OOM, model errors, etc.)
            error_msg = str(exc)
            if "out of memory" in error_msg.lower() or "oom" in error_msg.lower():
                raise LLMValidationError(
                    f"LlamaCppBackend: memoria insufficiente. "
                    f"Prova un modello più piccolo o riduci n_ctx. Dettaglio: {exc}"
                ) from exc
            raise LLMValidationError(f"LlamaCppBackend error: {exc}") from exc


# ── Factory ───────────────────────────────────────────────────────────────────

class BackendFactory:

    @staticmethod
    def create(backend_name: str, **kwargs) -> LLMBackend:
        if backend_name == "local_ollama":
            return OllamaBackend(**kwargs)
        elif backend_name == "openai":
            return OpenAIBackend(**kwargs)
        elif backend_name == "claude":
            return ClaudeBackend(**kwargs)
        elif backend_name == "openai_compatible":
            return OpenAICompatibleBackend(**kwargs)
        elif backend_name == "local_llama_cpp":
            return LlamaCppBackend(**kwargs)
        else:
            raise ValueError(f"Unknown backend: {backend_name}")

    @staticmethod
    def from_env() -> LLMBackend:
        name = os.getenv("LLM_BACKEND", "local_ollama")
        return BackendFactory.create(name)


# ── Circuit breaker ───────────────────────────────────────────────────────────

def call_with_fallback(
    primary: LLMBackend,
    system_prompt: str,
    user_prompt: str,
    json_schema: dict[str, Any],
    temperature: float = 0.0,
    fallback: LLMBackend | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """
    Try primary backend; on failure try fallback (OllamaBackend).
    Returns (result_dict, backend_name_used) or (None, 'quarantine').
    """
    for backend in filter(None, [primary, fallback]):
        try:
            logger.debug(f"Attempting LLM completion with backend: {backend.name}")
            result = backend.complete_structured(
                system_prompt, user_prompt, json_schema, temperature
            )
            return result, backend.name
        except LLMValidationError as exc:
            logger.warning(f"Backend {backend.name} failed: {exc}")
    return None, "quarantine"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_required(data: dict, schema: dict) -> None:
    required = schema.get("required", [])
    for field in required:
        if field not in data:
            raise LLMValidationError(f"Missing required field: {field}")
