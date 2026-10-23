from typing import Optional

import requests

from core.config import settings
from core.llm.base import LLMResult


class OllamaClient:
    name = "ollama"

    def __init__(self, host: str = settings.ollama_host, model: str = settings.ollama_model):
        self.host = host.rstrip("/")
        self.model = model

    def complete(self, system: str, user: str, *, json_schema: Optional[dict] = None,
                 temperature: float = 0.2) -> LLMResult:
        payload = {
            "model": self.model,
            "stream": False,
            # Ollama's default context (2-4k tokens) is smaller than our review prompts, so set it explicitly.
            "options": {"temperature": temperature, "num_ctx": settings.ollama_num_ctx},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_schema:
            payload["format"] = json_schema
        r = requests.post(f"{self.host}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        return LLMResult(text=r.json()["message"]["content"], model=self.model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        r = requests.post(f"{self.host}/api/embed", json={"model": self.model, "input": texts}, timeout=120)
        r.raise_for_status()
        return r.json()["embeddings"]
