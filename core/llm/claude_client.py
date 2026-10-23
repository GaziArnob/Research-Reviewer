from core.llm.base import LLMResult


class ClaudeClient:
    """Placeholder for the optional paid upgrade. Not used while the project is free-only."""

    name = "claude"

    def complete(self, system, user, *, json_schema=None, temperature=0.2) -> LLMResult:
        raise NotImplementedError("Claude client is a later, optional upgrade.")

    def embed(self, texts):
        raise NotImplementedError("Claude client is a later, optional upgrade.")
