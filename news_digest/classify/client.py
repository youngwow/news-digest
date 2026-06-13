"""OpenAI-compatible chat-completions client with retries and usage tracking."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

import httpx

from ..config import Provider
from ..jsonio import get_logger

log = get_logger("llm_client")

# Only these 4xx plus any 5xx are worth retrying; other 4xx (401 bad key,
# 400 bad request) fail identically on every attempt.
_RETRYABLE_STATUS = {408, 429}


class UsageTracker:
    """Thread-safe token-usage accumulator across parallel chunk workers."""

    def __init__(self):
        self._lock = Lock()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.api_calls = 0

    def reset(self) -> None:
        with self._lock:
            self.prompt_tokens = self.completion_tokens = self.api_calls = 0

    def add(self, usage: dict) -> None:
        with self._lock:
            self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
            self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
            self.api_calls += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"prompt_tokens": self.prompt_tokens,
                    "completion_tokens": self.completion_tokens,
                    "api_calls": self.api_calls}


@dataclass
class CompletionResult:
    """Outcome of one completion call. content is None on any failure."""
    content: str | None
    error: str | None = None  # "http" | "truncated" | "malformed" | None


class LLMClient:
    """Wraps one provider's chat-completions endpoint.

    Inject `transport` (an httpx.MockTransport) in tests to avoid real network.
    """

    def __init__(self, provider: Provider, api_key: str | None = None,
                 usage: UsageTracker | None = None,
                 transport: httpx.BaseTransport | None = None):
        self.provider = provider
        self.api_key = api_key
        self.usage = usage or UsageTracker()
        self._transport = transport

    def _client(self) -> httpx.Client:
        kwargs = {"timeout": self.provider.timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def complete(self, label: str, prompt_text: str, sleep=time.sleep) -> CompletionResult:
        """Run one completion with HTTP retries. Records usage; flags truncation."""
        p = self.provider
        resp: httpx.Response | None = None
        with self._client() as client:
            for attempt in range(1, p.max_retries + 1):
                try:
                    resp = client.post(
                        f"{p.base_url}/chat/completions",
                        headers=self._headers(),
                        json={"model": p.model,
                              "messages": [{"role": "user", "content": prompt_text}],
                              "temperature": p.temperature, "max_tokens": p.max_tokens},
                    )
                    if resp.status_code == 200:
                        break
                    if resp.status_code not in _RETRYABLE_STATUS and resp.status_code < 500:
                        log.error("%s: HTTP %d (not retryable): %s",
                                  label, resp.status_code, resp.text[:200])
                        return CompletionResult(None, "http")
                    log.warning("%s attempt %d/%d: HTTP %d: %s",
                                label, attempt, p.max_retries, resp.status_code, resp.text[:200])
                except httpx.RequestError as e:
                    log.warning("%s attempt %d/%d: request error: %s",
                                label, attempt, p.max_retries, e)
                    resp = None
                if attempt < p.max_retries:
                    sleep(2 ** attempt)
            else:
                log.error("%s: all %d retries exhausted", label, p.max_retries)
                return CompletionResult(None, "http")

        if resp is None or resp.status_code != 200:
            return CompletionResult(None, "http")

        try:
            result = resp.json()
            choice = result["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            log.error("%s: malformed API response envelope (%s): %s", label, e, resp.text[:300])
            return CompletionResult(None, "malformed")

        self.usage.add(result.get("usage") or {})

        if choice.get("finish_reason") == "length":
            log.error("%s: response truncated at max_tokens=%d — stories would be silently "
                      "lost; increase max_tokens for provider '%s'",
                      label, p.max_tokens, p.name)
            return CompletionResult(None, "truncated")

        return CompletionResult(content)
