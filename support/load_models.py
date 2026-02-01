import support.settings as settings

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint
from langchain_openai import ChatOpenAI


def create_model(model_name: str):
    """Factory function to create model instances based on model name."""

    # OpenAI models
    if model_name.startswith("gpt-"):
        if model_name == "gpt-4.1-nano":
            return ChatOpenAI(
                model=model_name,
                temperature=0,
                timeout=None,
                max_retries=1,
                streaming=True,
            )
        else:
            reasoning = {
                "effort": "low",  # 'low', 'medium', or 'high'
                "summary": "auto",  # 'detailed', 'auto', or None
            }
            return ChatOpenAI(
                model=model_name,
                temperature=0,
                timeout=None,
                max_retries=1,
                streaming=True,
                reasoning=reasoning
            )

    # Google models
    elif model_name.startswith("gemini-"):
        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0,
            timeout=None,
            max_retries=1,
            streaming=True,
            include_thoughts=True 
        )

    # Anthropic models
    elif model_name.startswith("claude-"):
        return ChatAnthropic(
            model=model_name,
            temperature=0,
            timeout=None,
            max_retries=1,
            streaming=True,
        )

    elif model_name in ["openai/gpt-oss-120b", "deepseek-ai/DeepSeek-V3.1", "Qwen/Qwen3-235B-A22B-Instruct-2507", "openai/gpt-oss-20b", "moonshotai/Kimi-K2-Thinking"]:
        provider_dict = {
            "openai/gpt-oss-120b": "novita",
            "deepseek-ai/DeepSeek-V3.1": "novita",
            "Qwen/Qwen3-235B-A22B-Instruct-2507": "novita",
            "openai/gpt-oss-20b": "together",
            "moonshotai/Kimi-K2-Thinking": "together",
        }

        llm = HuggingFaceEndpoint(
            repo_id=model_name,
            task="text-generation",
            provider=provider_dict[model_name],
            streaming=True,
            reasoning=True,
            temperature=0,
            top_p=0
        )

        return ChatHuggingFace(llm=llm, verbose=True)

    elif model_name in ["gemma3:12b", "mannix/llama3-12b:latest"]:
        from langchain_ollama import ChatOllama

        llm = ChatOllama(
            api_key="ollama",
            model=model_name,  # model_dict[modelName],
            temperature=0,
            num_ctx=5000,
            max_tokens=-1,
            reasoning=True,
            base_url="http://localhost:11434",
        )

        return llm

    else:
        raise ValueError(f"Unknown model: {model_name}")

