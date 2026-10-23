from core.config import settings
from core.llm.base import LLMClient, NoLLM


def get_llm() -> LLMClient:
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        from core.llm.ollama_client import OllamaClient
        return OllamaClient()
    if provider == "claude":
        from core.llm.claude_client import ClaudeClient
        return ClaudeClient()
    return NoLLM()
