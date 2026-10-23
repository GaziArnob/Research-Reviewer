from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class LLMResult:
    text: str
    model: str


class LLMClient(Protocol):
    """Every LLM call in the project goes through this interface (Ollama today, Claude later)."""

    name: str

    def complete(self, system: str, user: str, *, json_schema: Optional[dict] = None,
                 temperature: float = 0.2) -> LLMResult: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class NoLLM:
    """Phase 1 placeholder: the rule-based pipeline never calls a model."""

    name = "none"

    def complete(self, *args, **kwargs) -> LLMResult:
        raise RuntimeError("No LLM configured. Set LLM_PROVIDER=ollama in .env to enable LLM steps.")

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("No LLM configured.")
