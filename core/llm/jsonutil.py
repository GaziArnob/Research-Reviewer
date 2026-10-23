import json
import re
from typing import Optional

from core.llm.base import LLMClient


def extract_json(text: str) -> Optional[dict]:
    """Parse a JSON object from model output, tolerating code fences and surrounding prose."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, list):  # small models sometimes wrap the object in a list
            data = next((d for d in data if isinstance(d, dict)), None)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    wrapped = re.match(r"^\[\s*(\{.*\})\s*\]?\s*$", text, re.S)  # '[ {...}' with a missing closing bracket
    if wrapped:
        text = wrapped.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            return data if isinstance(data, dict) else None
        except ValueError:
            return None
    return None


def ask_json(llm: LLMClient, system: str, user: str, schema: Optional[dict] = None,
             retries: int = 1, temperature: float = 0.2) -> Optional[dict]:
    """Call the model and return parsed JSON, retrying once with a stricter reminder."""
    prompt = user
    for _ in range(retries + 1):
        try:
            out = llm.complete(system, prompt, json_schema=schema, temperature=temperature)
        except Exception:
            return None
        data = extract_json(out.text)
        if data is not None:
            return data
        prompt = user + "\n\nReturn ONLY one valid JSON object that follows the requested format. No other text."
    return None
