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
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "response": json.dumps({"category": "Alimentari", "subcategory": "Spesa supermercato", "confidence": "high"})
        }
        schema = {"required": ["category", "subcategory", "confidence"]}
        with patch.object(b._requests, "post", return_value=mock_resp) as mock_post:
            result = b.complete_structured("sys", "user", schema)
            assert result["category"] == "Alimentari"
            call_url = mock_post.call_args[0][0]
            assert "/api/generate" in call_url

    def test_complete_structured_invalid_json_raises(self):
        b = OllamaBackend()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"response": "not json"}
        with patch.object(b._requests, "post", return_value=mock_resp):
            with pytest.raises(LLMValidationError):
                b.complete_structured("sys", "user", {})
