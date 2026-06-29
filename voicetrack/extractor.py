import json
import socket
import threading
import urllib.parse
import requests

from voicetrack.config import (
    OLLAMA_HOST,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
)
from voicetrack.prompts import EXTRACTOR_SYSTEM_PROMPT, ORCHESTRATOR_SYSTEM_PROMPT

# Hard wall-clock deadline for each LLM call (seconds).
# Ollama typically takes 5-15s depending on hardware; 20s covers slow machines.
_LLM_DEADLINE = 20.0


class OllamaError(Exception):
    pass


def _ollama_reachable() -> bool:
    """Quick socket check — returns in <0.5 s."""
    parsed = urllib.parse.urlparse(OLLAMA_HOST)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _call_ollama(model: str, system_prompt: str, user_content: str) -> dict:
    url = f"{OLLAMA_HOST}/api/chat"
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
    }
    try:
        response = requests.post(url, json=payload, timeout=(2, OLLAMA_TIMEOUT))
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise OllamaError(str(e)) from e

    data = response.json()
    content = data["message"]["content"]
    return json.loads(content)


def _call_with_deadline(model: str, system_prompt: str, user_content: str) -> dict | None:
    """Run an Ollama call in a thread; return None if it exceeds _LLM_DEADLINE."""
    result_box: list = []
    error_box:  list = []

    def _worker():
        try:
            result_box.append(_call_ollama(model, system_prompt, user_content))
        except Exception as e:
            error_box.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=_LLM_DEADLINE)

    if result_box:
        return result_box[0]
    return None


def extract(user_input: str) -> dict:
    """
    1. If Ollama is reachable: run extractor LLM, then validate with orchestrator.
    2. If either step times out or Ollama is down: use fast regex fallback.
    """
    from voicetrack import fallback as _fallback

    if _ollama_reachable():
        # ── Step 1: extractor ────────────────────────────────
        raw = _call_with_deadline(OLLAMA_MODEL, EXTRACTOR_SYSTEM_PROMPT, user_input)

        if raw is not None and "error" not in raw:
            # ── Step 2: orchestrator validates & corrects ────
            orchestrator_input = (
                f"Original user text: {user_input}\n"
                f"Extracted JSON: {json.dumps(raw)}"
            )
            validated = _call_with_deadline(
                OLLAMA_MODEL, ORCHESTRATOR_SYSTEM_PROMPT, orchestrator_input
            )
            if validated is not None and "error" not in validated:
                return validated
            # Orchestrator timed out — return extractor result as-is
            raw.setdefault("confidence", "low")
            return raw

    # ── Fallback: instant regex parser ──────────────────────
    result = _fallback.parse(user_input)
    # Wrap in transactions key if it's a list result
    if "transactions" not in result:
        result["confidence"] = "low"
    else:
        for tx in result["transactions"]:
            tx.setdefault("confidence", "low")
    return result


def normalize_transactions(result: dict) -> list:
    if "transactions" in result:
        return result["transactions"]
    if "error" in result:
        return []
    return [result]
