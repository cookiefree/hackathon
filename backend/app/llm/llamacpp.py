import json
import threading

import requests

from app import config
from app.llm.base import LLMClient, LLMContextExceeded, LLMError, LLMUnavailable

START_HINT = "Start it with C:\\llm\\run-server.ps1 and wait until it prints 'listening'."


class LlamaCppLLM(LLMClient):
    """Local llama.cpp server (OpenAI-compatible /v1/chat/completions) with a JSON-schema response.

    The server has a single slot, so requests are serialised with a lock. Nothing connects in
    the constructor: the app has to start even while llama-server is down.
    """
    name = "llamacpp"

    def __init__(self) -> None:
        self.base_url = config.get("LLAMACPP_URL", "http://127.0.0.1:8080").rstrip("/")
        self.timeout = float(config.get("LLAMACPP_TIMEOUT", "180"))
        self.max_tokens = int(config.get("LLAMACPP_MAX_TOKENS", "2048"))
        self._lock = threading.Lock()

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "lesson_note", "strict": True, "schema": schema},
            },
            # Thinking is off server-side by default; say it explicitly anyway.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        with self._lock:
            try:
                r = requests.post(f"{self.base_url}/v1/chat/completions", json=body,
                                  timeout=(5, self.timeout))
            except requests.ConnectionError as e:
                raise LLMUnavailable(
                    f"llama-server is not reachable at {self.base_url}. {START_HINT}") from e
            except requests.Timeout as e:
                raise LLMError(
                    f"llama-server did not answer within {self.timeout:.0f}s "
                    f"(LLAMACPP_TIMEOUT). It may be overloaded or the transcript is very long.") from e

        if r.status_code != 200:
            self._raise_http_error(r)

        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMError(f"The model's answer was cut off after {self.max_tokens} tokens "
                           f"(LLAMACPP_MAX_TOKENS), so the JSON is incomplete.")
        try:
            return json.loads(choice["message"]["content"])
        except json.JSONDecodeError as e:
            raise LLMError(f"llama-server returned invalid JSON: {e}") from e

    @staticmethod
    def _raise_http_error(r: requests.Response) -> None:
        try:
            err = r.json().get("error", {})
        except ValueError:
            err = {}
        msg = err.get("message") or r.text[:300]
        if err.get("type") == "exceed_context_size_error" or "context" in msg.lower() and "exceed" in msg.lower():
            n, ctx = err.get("n_prompt_tokens"), err.get("n_ctx")
            detail = f" ({n} tokens, context is {ctx})" if n and ctx else ""
            raise LLMContextExceeded(
                f"The transcript is too long for the model's context window{detail}. "
                f"Split it into parts of roughly 25k tokens or fewer.")
        raise LLMError(f"llama-server returned HTTP {r.status_code}: {msg}")
