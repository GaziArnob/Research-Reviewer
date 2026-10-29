"""Sentence-level "how predictable is this text" scoring with a small local language model (GPT-2 small, ONNX).

AI-written text tends to be more predictable to a language model than human text: its words are more often the model's
first choices. For every sentence we measure (a) the perplexity and (b) the share of words that were among the model's
10 most likely choices (the idea behind GLTR). This is a hint, not proof: formal or edited human text can look
predictable, and paraphrased AI text can look human.
"""
import math
import re
from pathlib import Path
from typing import Optional

import numpy as np

MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "gpt2"
_WINDOW = 512
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(\[])")
_state: dict = {}


def available() -> bool:
    return (MODEL_DIR / "tokenizer.json").exists() and any((MODEL_DIR / n).exists() for n in ("decoder_model.onnx", "decoder_model_quantized.onnx"))


def _load(prefer_full: bool = True):
    if "session" in _state:
        return _state["session"], _state["tok"]
    import onnxruntime as ort
    from tokenizers import Tokenizer
    names = ["decoder_model.onnx", "decoder_model_quantized.onnx"] if prefer_full else ["decoder_model_quantized.onnx", "decoder_model.onnx"]
    path = next((MODEL_DIR / n for n in names if (MODEL_DIR / n).exists() and (MODEL_DIR / n).stat().st_size > 50_000_000), None)
    if path is None or not (MODEL_DIR / "tokenizer.json").exists():
        raise RuntimeError("The GPT-2 model files are missing in models/gpt2.")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    _state["session"] = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
    _state["tok"] = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    _state["model_file"] = path.name
    return _state["session"], _state["tok"]


def model_name() -> str:
    _load()
    return _state["model_file"]


def _token_stats(ids: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """Per token: log-probability and whether it was in the model's top 10. Windows of 512 tokens, each starting with <|endoftext|>."""
    sess, _ = _load()
    names = [i.name for i in sess.get_inputs()]
    logps, top10 = [], []
    step = _WINDOW - 1
    for start in range(0, len(ids), step):
        chunk = [50256] + ids[start:start + step]
        feed = {"input_ids": np.asarray([chunk], dtype=np.int64)}
        if "attention_mask" in names:
            feed["attention_mask"] = np.ones((1, len(chunk)), dtype=np.int64)
        logits = sess.run(None, feed)[0][0][:-1].astype(np.float64)  # prediction for tokens 1..n
        target = np.asarray(chunk[1:])
        logits -= logits.max(axis=1, keepdims=True)
        lse = np.log(np.exp(logits).sum(axis=1))
        lp = logits[np.arange(len(target)), target] - lse
        rank = (logits > logits[np.arange(len(target)), target][:, None]).sum(axis=1)
        logps.append(lp)
        top10.append(rank < 10)
    return np.concatenate(logps), np.concatenate(top10)


def score_text(text: str, max_tokens: int = 6000) -> Optional[dict]:
    """{sentences: [{text, ppl, top10, tokens}], doc_ppl, doc_top10, tokens}. None if the text is too short to score."""
    _, tok = _load()
    flat = " ".join(text.split())
    enc = tok.encode(flat, add_special_tokens=False)
    if len(enc.ids) < 60:
        return None
    ids, offsets = enc.ids[:max_tokens], enc.offsets[:max_tokens]
    lp, hit = _token_stats(ids)
    sentences, pos = [], 0
    for part in _SENT.split(flat):
        start = flat.find(part, pos)
        end = start + len(part)
        pos = end
        idx = [i for i, (a, b) in enumerate(offsets) if a >= start and b <= end]
        if len(idx) < 6:
            continue
        mean_lp = float(lp[idx].mean())
        sentences.append({"text": part, "ppl": math.exp(-mean_lp), "top10": float(hit[idx].mean()), "tokens": len(idx)})
    if not sentences:
        return None
    return {"sentences": sentences, "doc_ppl": math.exp(-float(lp.mean())), "doc_top10": float(hit.mean()), "tokens": len(ids)}
