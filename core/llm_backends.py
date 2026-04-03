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

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

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

    # ── Token usage tracking ──────────────────────────────────────────────
    # last_usage: populated after each complete_structured() call.
    # cumulative_usage: accumulated across multiple calls (reset with reset_cumulative_usage).
    last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    cumulative_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _reset_usage(self) -> None:
        self.last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _set_usage(self, prompt: int, completion: int) -> None:
        self.last_usage = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
        self.cumulative_usage = {
            "prompt_tokens": self.cumulative_usage.get("prompt_tokens", 0) + prompt,
            "completion_tokens": self.cumulative_usage.get("completion_tokens", 0) + completion,
            "total_tokens": self.cumulative_usage.get("total_tokens", 0) + prompt + completion,
        }

    def reset_cumulative_usage(self) -> None:
        """Reset cumulative counters (call before each benchmark file)."""
        self.cumulative_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def get_context_info(self) -> dict | None:
        """Return context window info for this backend/model, or None if unavailable.

        Returns a dict with any subset of:
          n_ctx        – currently configured context window (tokens)
          n_ctx_train  – model's native maximum context (tokens)
        """
        return None


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
        self._reset_usage()
        try:
            resp = self._requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["response"]
            self._set_usage(
                data.get("prompt_eval_count", 0),
                data.get("eval_count", 0),
            )
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._requests.RequestException, KeyError, json.JSONDecodeError) as exc:
            raise LLMValidationError(f"OllamaBackend error: {exc}") from exc

    @staticmethod
    def fetch_context_length(model: str, base_url: str = "http://localhost:11434") -> int | None:
        """Query /api/show and return the model's native context length (no instance needed)."""
        try:
            import requests as _req
            resp = _req.post(
                f"{base_url.rstrip('/')}/api/show",
                json={"model": model},
                timeout=5,
            )
            resp.raise_for_status()
            info = resp.json().get("model_info", {})
            ctx = info.get("llama.context_length") or info.get("context_length")
            return int(ctx) if ctx else None
        except Exception:
            return None

    def get_context_info(self) -> dict | None:
        ctx = OllamaBackend.fetch_context_length(self.model, self.base_url)
        return {"n_ctx_train": ctx} if ctx else None

    def is_available(self) -> bool:
        try:
            resp = self._requests.get(f"{self.base_url}/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False


# ── Known context windows for remote models ───────────────────────────────────
_KNOWN_CONTEXT: dict[str, int] = {
    # OpenAI
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    # Claude
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
}


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

    def get_context_info(self) -> dict | None:
        ctx = _KNOWN_CONTEXT.get(self.model)
        return {"n_ctx_train": ctx} if ctx else None

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
        self._reset_usage()
        client = self._openai.OpenAI(api_key=self.api_key, timeout=self.timeout)
        # OpenAI strict mode requires: all properties in required + additionalProperties=false
        _schema = json.loads(json.dumps(json_schema))  # deep copy
        def _fix_strict(obj):
            if isinstance(obj, dict):
                if "properties" in obj:
                    obj["required"] = list(obj["properties"].keys())
                    obj["additionalProperties"] = False
                for v in obj.values():
                    _fix_strict(v)
            elif isinstance(obj, list):
                for item in obj:
                    _fix_strict(item)
        _fix_strict(_schema)
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "response", "schema": _schema, "strict": True},
                },
                temperature=temperature,
            )
            if response.usage:
                self._set_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
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

    def get_context_info(self) -> dict | None:
        ctx = _KNOWN_CONTEXT.get(self.model)
        return {"n_ctx_train": ctx} if ctx else None

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
        self._reset_usage()
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
            if response.usage:
                self._set_usage(response.usage.input_tokens, response.usage.output_tokens)
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
        self._reset_usage()
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
            if response.usage:
                self._set_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
            content = response.choices[0].message.content
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._openai.OpenAIError, json.JSONDecodeError, KeyError) as exc:
            raise LLMValidationError(f"OpenAICompatibleBackend error: {exc}") from exc


# ── vLLM (local or remote, OpenAI-compatible with guided decoding) ─────────────

class VllmBackend(LLMBackend):
    """vLLM backend — uses the OpenAI-compatible /v1/chat/completions endpoint
    with guided JSON decoding (extra_body.guided_json) for structured output.

    Works with both local `vllm serve` and remote vLLM instances.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str = "EMPTY",
        timeout: int = 120,
    ):
        import openai as _openai
        self._openai = _openai
        self.base_url = (base_url or os.getenv("VLLM_BASE_URL", "http://localhost:8000/v1")).rstrip("/")
        self.model = model or os.getenv("VLLM_MODEL", "")
        self.api_key = api_key
        self.timeout = timeout

    @property
    def is_remote(self) -> bool:
        # Treat as local by default — user can override via subclassing if needed
        return "localhost" not in self.base_url and "127.0.0.1" not in self.base_url

    @property
    def name(self) -> str:
        return "vllm"

    @staticmethod
    def fetch_context_length(base_url: str, model: str = "", api_key: str = "EMPTY") -> int | None:
        """Query the vLLM /v1/models endpoint and return context_length if exposed."""
        try:
            import openai as _openai
            client = _openai.OpenAI(
                api_key=api_key,
                base_url=base_url.rstrip("/"),
                timeout=5,
            )
            models = client.models.list()
            target = model or (models.data[0].id if models.data else None)
            if not target:
                return None
            # vLLM exposes context_length on the model object when available
            for m in models.data:
                if m.id == target:
                    ctx = getattr(m, "context_length", None) or getattr(m, "max_context_length", None)
                    return int(ctx) if ctx else None
        except Exception:
            pass
        return None

    def get_context_info(self) -> dict | None:
        ctx = VllmBackend.fetch_context_length(self.base_url, self.model, self.api_key)
        return {"n_ctx_train": ctx} if ctx else None

    def _auto_detect_model(self) -> str:
        """Query /v1/models and pick the first (and usually only) served model."""
        client = self._openai.OpenAI(
            api_key=self.api_key, base_url=self.base_url, timeout=10,
        )
        models = client.models.list()
        if models.data:
            return models.data[0].id
        raise LLMValidationError("vLLM: nessun modello servito. Lancia vllm serve prima.")

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        self._reset_usage()
        if not self.model:
            self.model = self._auto_detect_model()

        client = self._openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
        )
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                extra_body={"guided_json": json_schema},
            )
            if response.usage:
                self._set_usage(response.usage.prompt_tokens, response.usage.completion_tokens)
            content = response.choices[0].message.content
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (self._openai.OpenAIError, json.JSONDecodeError, KeyError) as exc:
            raise LLMValidationError(f"VllmBackend error: {exc}") from exc

    def is_available(self) -> bool:
        try:
            client = self._openai.OpenAI(
                api_key=self.api_key, base_url=self.base_url, timeout=5,
            )
            client.models.list()
            return True
        except Exception:
            return False


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
    "gemma-4-E2B-it-Q4_K_M": {
        "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q4_K_M.gguf",
        "size_gb": 3.1,
        "description": "Google Gemma 4 E2B — qualità/velocità bilanciati (consigliato)",
    },
    "gemma-4-E2B-it-Q3_K_M": {
        "url": "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-Q3_K_M.gguf",
        "size_gb": 2.5,
        "description": "Google Gemma 4 E2B — versione compressa, per Mac con RAM limitata",
    },
    "Qwen3.5-2B-Q4_K_M": {
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3.5-2B-GGUF/resolve/main/Qwen3.5-2B-Q4_K_M.gguf",
        "size_gb": 1.7,
        "description": "Qwen 3.5 2B — leggero, ottimo rapporto qualità/dimensione",
    },
    "Qwen3.5-4B-Q4_K_M": {
        "url": "https://huggingface.co/bartowski/Qwen_Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
        "size_gb": 2.5,
        "description": "Qwen 3.5 4B — bilanciato qualità/velocità",
    },
}


class LlamaCppBackend(LLMBackend):
    """Local LLM backend using llama-cpp-python. No external service needed."""

    def __init__(
        self,
        model_path: str | None = None,
        n_ctx: int = 0,
        n_gpu_layers: int = -1,
        timeout: int = 120,
    ):
        """
        Args:
            model_path: Path to a .gguf file. None = auto-detect from ~/.spendify/models/.
            n_ctx: Context window size in tokens.
                   0 (default) = auto-detect from GGUF metadata (model's native max),
                   falling back to 4096 if detection fails.
            n_gpu_layers: GPU layers to offload (-1 = all).
        """
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
        if n_ctx == 0:
            n_ctx = LlamaCppBackend.read_gguf_context_length(model_path) or 4096
        self._model_path = model_path
        self._llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            n_batch=1024,       # larger batch → better GPU utilization on Metal
            n_threads=1,        # with full GPU offload, CPU threads not needed for inference
            flash_attn=True,    # faster attention on Metal (Apple Silicon)
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
    def model(self) -> str:
        """Return the GGUF filename (without extension) as model identifier."""
        return Path(self._model_path).stem

    @property
    def model_size_bytes(self) -> int:
        """Return the GGUF file size in bytes."""
        return Path(self._model_path).stat().st_size

    @staticmethod
    def read_gguf_context_length(model_path: str) -> int | None:
        """Read context_length from a GGUF file header without loading model weights.

        Searches for any key ending in '.context_length' — covers all known architectures:
        llama.context_length, qwen2.context_length, gemma4.context_length,
        phi3.context_length, mistral.context_length, …

        Parses the binary GGUF metadata section using struct — fast (reads only the header,
        no GPU allocation, no tokenizer loading).  Returns None if the file is not a valid
        GGUF or the key is absent.
        """
        import struct

        _FIXED = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

        def _skip(f, vtype: int) -> None:
            if vtype in _FIXED:
                f.read(_FIXED[vtype])
            elif vtype == 8:                          # string
                f.read(struct.unpack("<Q", f.read(8))[0])
            elif vtype == 9:                          # array
                atype = struct.unpack("<I", f.read(4))[0]
                n    = struct.unpack("<Q", f.read(8))[0]
                for _ in range(n):
                    _skip(f, atype)
            # unknown types: silently stop — caller catches Exception

        try:
            with open(model_path, "rb") as f:
                if f.read(4) != b"GGUF":
                    return None
                f.read(4 + 8)                         # version + n_tensors
                n_kv = struct.unpack("<Q", f.read(8))[0]
                for _ in range(n_kv):
                    klen = struct.unpack("<Q", f.read(8))[0]
                    key  = f.read(klen).decode("utf-8", errors="replace")
                    vtype = struct.unpack("<I", f.read(4))[0]
                    if key.endswith(".context_length"):  # works for any architecture prefix
                        return struct.unpack("<I", f.read(4))[0]  # uint32
                    _skip(f, vtype)
        except Exception:
            pass
        return None

    def get_context_info(self) -> dict | None:
        return {
            "n_ctx": self._llm.n_ctx(),
            "n_ctx_train": self._llm.n_ctx_train(),
        }

    @property
    def is_remote(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "local_llama_cpp"

    def _render_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """Render system+user messages using the model's Jinja2 chat template.

        Reads the template from GGUF metadata and applies it, exactly like
        Ollama does internally.  If the template rejects the system role
        (e.g. Gemma), the system content is prepended to the user message.
        Falls back to a simple concatenation if no template is available.
        """
        from jinja2 import Template, TemplateSyntaxError, UndefinedError

        raw_template = self._llm.metadata.get("tokenizer.chat_template", "")
        if not raw_template:
            # No template — simple format
            return f"{system_prompt}\n\n{user_prompt}"

        # Check if template supports system role by looking for raise_exception
        _supports_system = "raise_exception" not in raw_template or "system" not in raw_template

        if _supports_system:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        else:
            # Model rejects system role (e.g. Gemma) — prepend to user
            messages = [
                {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"},
            ]

        try:
            t = Template(raw_template)
            return t.render(
                messages=messages,
                bos_token=self._llm.metadata.get("tokenizer.ggml.bos_token_id", ""),
                eos_token=self._llm.metadata.get("tokenizer.ggml.eos_token_id", ""),
                add_generation_prompt=True,
            )
        except (TemplateSyntaxError, UndefinedError, TypeError):
            # Template rendering failed — try without system role
            messages = [
                {"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"},
            ]
            try:
                t = Template(raw_template)
                return t.render(
                    messages=messages,
                    bos_token="",
                    eos_token="",
                    add_generation_prompt=True,
                )
            except Exception:
                return f"{system_prompt}\n\n{user_prompt}"

    def complete_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        self._reset_usage()
        from llama_cpp import LlamaGrammar

        # Build a GBNF grammar from the JSON schema — forces the model to produce
        # output conforming to the schema structure (same approach as Ollama).
        try:
            grammar = LlamaGrammar.from_json_schema(
                json.dumps(json_schema), verbose=False
            )
        except Exception:
            grammar = None  # fallback: no grammar constraint

        try:
            # Render the prompt using the model's native Jinja2 chat template.
            # This mirrors Ollama's approach: apply the exact template the model
            # was trained with, then run raw completion with grammar enforcement.
            prompt = self._render_prompt(system_prompt, user_prompt)

            # Sampling params aligned with Ollama defaults for deterministic output
            response = self._llm.create_completion(
                prompt=prompt,
                grammar=grammar,
                temperature=temperature,
                top_p=0.9,
                top_k=40,
                repeat_penalty=1.1,
                max_tokens=2048,
                stop=["<|im_end|>", "<end_of_turn>", "</s>", "<|eot_id|>"],
            )
            usage = response.get("usage", {})
            self._set_usage(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
            raw = response["choices"][0]["text"]
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
        elif backend_name == "vllm":
            return VllmBackend(**kwargs)
        else:
            raise ValueError(f"Unknown backend: {backend_name}")

    @staticmethod
    def from_env() -> LLMBackend:
        name = os.getenv("LLM_BACKEND", "local_llama_cpp")
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
