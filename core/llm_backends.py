from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
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
    """Local Ollama backend via OpenAI-compatible API (/v1).

    Ollama exposes a fully OpenAI-compatible endpoint at <base_url>/v1
    (available since Ollama v0.1.33).  Using the openai SDK here avoids
    any dependency on native Ollama routes (/api/generate, /api/chat)
    which may differ across Ollama versions.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 60,
    ):
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
        import openai as _openai

        client = _openai.OpenAI(
            base_url=f"{self.base_url}/v1",
            api_key="ollama",          # required by the SDK, ignored by Ollama
            timeout=self.timeout,
        )
        # Include the schema in the system prompt so the model knows the
        # expected shape; use json_object mode for broad version compatibility.
        schema_hint = json.dumps(json_schema, ensure_ascii=False)
        augmented_system = (
            f"{system_prompt}\n\n"
            f"Respond with valid JSON that conforms to this JSON Schema:\n{schema_hint}"
        )
        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": augmented_system},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=temperature,
            )
            content = response.choices[0].message.content
            result = json.loads(content)
            _validate_required(result, json_schema)
            return result
        except (_openai.OpenAIError, json.JSONDecodeError, KeyError) as exc:
            raise LLMValidationError(f"OllamaBackend error: {exc}") from exc

    def is_available(self) -> bool:
        try:
            import requests as _r
            resp = _r.get(f"{self.base_url}/api/tags", timeout=3)
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
