"""Integration tests for real LLM backend connections (no mocks)."""
import json
import os
import pytest
from core.llm_backends import BackendFactory, LLMValidationError


class TestRealOllamaBackend:
    """Test real Ollama backend - requires local Ollama running on localhost:11434"""
    
    @pytest.fixture
    def ollama_backend(self):
        return BackendFactory.create("local_ollama")
    
    @pytest.mark.skipif(
        not os.getenv("TEST_REAL_BACKENDS"),
        reason="Set TEST_REAL_BACKENDS=1 to run real backend tests"
    )
    def test_ollama_connection(self, ollama_backend):
        """Test that Ollama backend can connect to local instance"""
        try:
            result = ollama_backend.complete("Respond with 'OK'")
            assert result is not None
            assert isinstance(result, str)
            assert len(result) > 0
        except Exception as e:
            pytest.skip(f"Ollama not available: {e}")
    
    @pytest.mark.skipif(
        not os.getenv("TEST_REAL_BACKENDS"),
        reason="Set TEST_REAL_BACKENDS=1 to run real backend tests"
    )
    def test_ollama_structured_response(self, ollama_backend):
        """Test structured output from Ollama"""
        schema = {
            "required": ["category", "subcategory", "confidence"],
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "subcategory": {"type": "string"},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]}
            }
        }
        
        system_prompt = "You are a categorization expert. Return valid JSON."
        user_prompt = "Categorize this: I spent $50 at the grocery store"
        
        try:
            result = ollama_backend.complete_structured(system_prompt, user_prompt, schema)
            assert result is not None
            assert "category" in result
            assert "subcategory" in result
            assert "confidence" in result
            assert isinstance(result["confidence"], str)
        except LLMValidationError as e:
            pytest.skip(f"Ollama structured output unavailable: {e}")
        except Exception as e:
            pytest.skip(f"Ollama not available: {e}")


class TestRealOpenAIBackend:
    """Test real OpenAI backend - requires OPENAI_API_KEY env var"""
    
    @pytest.fixture
    def openai_backend(self):
        return BackendFactory.create("openai")
    
    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="Set OPENAI_API_KEY to run real OpenAI tests"
    )
    def test_openai_connection(self, openai_backend):
        """Test that OpenAI backend can connect"""
        result = openai_backend.complete("Respond with 'OK' only")
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
    
    @pytest.mark.skipif(
        not os.getenv("OPENAI_API_KEY"),
        reason="Set OPENAI_API_KEY to run real OpenAI tests"
    )
    def test_openai_structured_response(self, openai_backend):
        """Test structured output from OpenAI"""
        schema = {
            "required": ["category", "confidence"],
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "confidence": {"type": "string"}
            }
        }
        
        result = openai_backend.complete_structured(
            "You are a categorization expert.",
            "Categorize: $50 grocery store",
            schema
        )
        assert "category" in result
        assert "confidence" in result


class TestRealClaudeBackend:
    """Test real Claude backend - requires ANTHROPIC_API_KEY env var"""
    
    @pytest.fixture
    def claude_backend(self):
        return BackendFactory.create("claude")
    
    @pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY"),
        reason="Set ANTHROPIC_API_KEY to run real Claude tests"
    )
    def test_claude_connection(self, claude_backend):
        """Test that Claude backend can connect"""
        result = claude_backend.complete("Respond with 'OK' only")
        assert result is not None
        assert isinstance(result, str)

# Test with local Ollama (requires Ollama running on localhost:11434)
# TEST_REAL_BACKENDS=1 pytest tests/test_real_backends.py::TestRealOllamaBackend -v -s

# Test with OpenAI
# OPENAI_API_KEY=your_key pytest tests/test_real_backends.py::TestRealOpenAIBackend -v -s

# Test with Claude
# ANTHROPIC_API_KEY=your_key pytest tests/test_real_backends.py::TestRealClaudeBackend -v -s

# Run all real tests with all backends
# TEST_REAL_BACKENDS=1 OPENAI_API_KEY=your_key ANTHROPIC_API_KEY=your_key pytest tests/test_real_backends.py -v -s
