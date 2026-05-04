"""Tests for services/llm_service.py — all LLM calls mocked."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestContextDetection:

    @patch("core.llm_backends.LlamaCppBackend")
    def test_detect_llama_cpp_context_with_path(self, mock_cls):
        from services.llm_service import detect_llama_cpp_context
        mock_cls.read_gguf_context_length.return_value = 8192
        result = detect_llama_cpp_context("/some/model.gguf")
        assert result == 8192

    @patch("core.llm_backends.OllamaBackend")
    def test_detect_ollama_context(self, mock_cls):
        from services.llm_service import detect_ollama_context
        mock_cls.fetch_context_length.return_value = 4096
        result = detect_ollama_context("gemma3:12b", "http://localhost:11434")
        assert result == 4096

    def test_get_known_context_window_unknown(self):
        from services.llm_service import get_known_context_window
        result = get_known_context_window("nonexistent-model-xyz")
        assert result is None

    @patch("core.llm_backends.VllmBackend")
    def test_detect_vllm_context(self, mock_cls):
        from services.llm_service import detect_vllm_context
        mock_cls.fetch_context_length.return_value = 16384
        result = detect_vllm_context("http://localhost:8000/v1", "my-model")
        assert result == 16384


class TestBackendFactory:

    @patch("core.llm_backends.BackendFactory")
    def test_create_backend(self, mock_factory):
        from services.llm_service import create_backend
        mock_factory.create.return_value = MagicMock()
        result = create_backend("local_ollama", model="gemma3:12b")
        assert result is not None


class TestLocalModels:

    @patch("core.llm_backends.LlamaCppBackend")
    def test_list_local_llama_cpp_models(self, mock_cls):
        from services.llm_service import list_local_llama_cpp_models
        mock_cls.list_local_models.return_value = [{"name": "test", "path": "/a/b.gguf", "size_gb": 1.0}]
        result = list_local_llama_cpp_models()
        assert len(result) == 1

    def test_get_default_gguf_models(self):
        from services.llm_service import get_default_gguf_models
        models = get_default_gguf_models()
        assert isinstance(models, dict)
        assert len(models) > 0

    @patch("core.llm_backends.LlamaCppBackend")
    def test_read_gguf_context_length(self, mock_cls):
        from services.llm_service import read_gguf_context_length
        mock_cls.read_gguf_context_length.return_value = 32768
        assert read_gguf_context_length("/model.gguf") == 32768


class TestHardwareDetection:

    def test_detect_system_hardware(self):
        from services.llm_service import detect_system_hardware
        hw = detect_system_hardware()
        assert "os" in hw
        assert "ram_gb" in hw
        assert hw["ram_gb"] > 0

    def test_list_available_models(self):
        from services.llm_service import list_available_models
        models = list_available_models()
        assert isinstance(models, list)

    def test_get_recommended_model(self):
        from services.llm_service import get_recommended_model
        model = get_recommended_model(8)
        assert model is not None
        assert hasattr(model, "name")


class TestDescriptionProfiles:

    def test_get_description_profiles_empty_db(self, tmp_path):
        from db.models import create_tables, get_engine
        from services.llm_service import get_description_profiles

        eng = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
        create_tables(eng)
        profiles = get_description_profiles(eng)
        assert isinstance(profiles, list)
        assert len(profiles) == 0


class TestLLMValidationError:

    def test_importable(self):
        from services.llm_service import LLMValidationError
        assert LLMValidationError is not None
        assert issubclass(LLMValidationError, Exception)
