import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    contact_email: str = os.getenv("CONTACT_EMAIL", "")
    semantic_scholar_key: str = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    llm_provider: str = os.getenv("LLM_PROVIDER", "none")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    ollama_num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
    min_words: int = int(os.getenv("MIN_WORDS", "500"))
    max_refs_to_verify: int = int(os.getenv("MAX_REFS_TO_VERIFY", "60"))
    online_checks: bool = _bool("ONLINE_CHECKS", True)
    ocr_enabled: bool = _bool("OCR_ENABLED", True)
    consensus_runs: int = int(os.getenv("LLM_CONSENSUS_RUNS", "1"))


settings = Settings()
