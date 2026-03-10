"""Unit tests for core/llm_backends.py."""
import json
from unittest.mock import MagicMock, patch

import pytest

from core.llm_backends import (
    BackendFactory,
    LLMValidationError,
    OllamaBackend,
    SanitizationRequiredError,
    _validate_required,
)


class TestValidateRequired:
    def test_passes_when_all_present(self):
        _validate_required({"a": 1, "b": 2}, {"required": ["a", "b"]})

    def test_raises_when_missing(self):
        with pytest.raises(LLMValidationError):
            _validate_required({"a": 1}, {"required": ["a", "b"]})

    def test_no_required_key(self):
        _validate_required({"x": 1}, {})  # should not raise


class TestBackendFactory:
    def test_creates_ollama(self):
        backend = BackendFactory.create("local_ollama")
        assert backend.name == "local_ollama"
        assert backend.is_remote is False

    def test_creates_openai(self):
        backend = BackendFactory.create("openai")
        assert backend.name == "openai"
        assert backend.is_remote is True

    def test_creates_claude(self):
        backend = BackendFactory.create("claude")
        assert backend.name == "claude"
        assert backend.is_remote is True

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            BackendFactory.create("unknown_backend")


class TestOllamaBackend:
    def test_is_not_remote(self):
        b = OllamaBackend()
        assert b.is_remote is False

    def test_complete_structured_success(self):
        b = OllamaBackend()
        payload = {"category": "Alimentari", "subcategory": "Spesa supermercato", "confidence": "high"}
        mock_message = MagicMock()
        mock_message.content = json.dumps(payload)
        mock_choice = MagicMock()
        mock_choice.message = mock_message
        mock_completion = MagicMock()
        mock_completion.choices = [mock_choice]

        schema = {"required": ["category", "subcategory", "confidence"]}
        with patch("core.llm_backends.OllamaBackend.complete_structured", return_value=payload):
            result = b.complete_structured("sys", "user", schema)
            assert result["category"] == "Alimentari"

    def test_complete_structured_uses_v1_endpoint(self):
        """OllamaBackend must instantiate OpenAI client with /v1 base_url."""
        b = OllamaBackend(base_url="http://localhost:11434")
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            payload = {"category": "Casa"}
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content=json.dumps(payload)))]
            )
            result = b.complete_structured("sys", "user", {"required": ["category"]})
            call_kwargs = mock_openai_cls.call_args[1]
            assert call_kwargs["base_url"] == "http://localhost:11434/v1"
            assert call_kwargs["api_key"] == "ollama"
            assert result["category"] == "Casa"

    def test_complete_structured_invalid_json_raises(self):
        b = OllamaBackend()
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="not json"))]
            )
            with pytest.raises(LLMValidationError):
                b.complete_structured("sys", "user", {})
