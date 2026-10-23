from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_VERSION = "5"  # bump when a prompt or LLM post-processing changes so cached results are not reused


@lru_cache(maxsize=None)
def load(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")
