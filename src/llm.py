"""LLM + embedding clients that meter every call (tokens, USD, seconds) for the Flat RAG vs GraphRAG benchmark.

Providers (pick with env vars, otherwise the first one in PROVIDER_ORDER that has an API key wins):

    LLM_PROVIDER        = openai | openrouter | gemini | anthropic | mwapi    (chat)
    EMBEDDING_PROVIDER  = openai | openrouter | gemini                (Anthropic has no embedding API)
    <PROVIDER>_CHAT_MODEL / <PROVIDER>_EMBEDDING_MODEL override the default models below.

MWAPI is an Anthropic-compatible gateway. Its SDK base URL must omit `/v1` because the
Anthropic client appends `/v1/messages` itself.

One run uses one provider for the whole benchmark — no mid-run failover, so cost/quality numbers stay comparable.
"""

from __future__ import annotations

import importlib
import os
import time
from dataclasses import dataclass, fields
from typing import Any

PROVIDERS = {
    "openai": {"key": "OPENAI_API_KEY", "base_url": None,
               "chat": "gpt-4o-mini", "embed": "text-embedding-3-small"},
    "openrouter": {"key": "OPENROUTER_API_KEY", "base_url": "https://openrouter.ai/api/v1",
                   "chat": "openai/gpt-4o-mini", "embed": "openai/text-embedding-3-small"},
    "gemini": {"key": "GEMINI_API_KEY", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
               "chat": "gemini-2.5-flash-lite", "embed": "gemini-embedding-001"},
    "anthropic": {"key": "ANTHROPIC_API_KEY", "base_url": None,
                  "chat": "claude-opus-5-5", "embed": None},
    "mwapi": {"key": "MWAPI_API_KEY", "base_url": "https://api.mwapi.dev",
              "chat": "claude-haiku-4-5-20251001", "embed": None},
}
PROVIDER_ORDER = ["openai", "openrouter", "gemini", "anthropic", "mwapi"]

# USD per 1M tokens (input, output). Check each provider's pricing page before reporting real numbers.
PRICES_PER_M = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    # Gemini embedding pricing intentionally omitted: the current pricing page does not list gemini-embedding-001.
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}

@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    seconds: float = 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(*(getattr(self, f.name) + getattr(other, f.name) for f in fields(self)))

    def __sub__(self, other: "Usage") -> "Usage":
        return Usage(*(getattr(self, f.name) - getattr(other, f.name) for f in fields(self)))

def price(model: str, input_tokens: int, output_tokens: int = 0) -> float:
    per_in, per_out = PRICES_PER_M.get(model.split("/")[-1], (0.0, 0.0))   # "openai/gpt-4o-mini" -> "gpt-4o-mini"
    return (input_tokens * per_in + output_tokens * per_out) / 1_000_000

def pick_provider(env_var: str, need_embeddings: bool) -> str:
    """Explicit env choice, else the first provider (in PROVIDER_ORDER) whose API key is set."""
    usable = [p for p in PROVIDER_ORDER if not need_embeddings or PROVIDERS[p]["embed"]]
    chosen = os.getenv(env_var, "").strip().lower()
    if chosen:
        if chosen not in usable:
            raise RuntimeError(f"{env_var}={chosen} không hợp lệ; chọn một trong: {', '.join(usable)}")
        if not os.getenv(PROVIDERS[chosen]["key"]):
            raise RuntimeError(f"{env_var}={chosen} nhưng chưa có {PROVIDERS[chosen]['key']} trong .env")
        return chosen
    for provider in usable:
        if os.getenv(PROVIDERS[provider]["key"]):
            return provider
    keys = " / ".join(PROVIDERS[p]["key"] for p in usable)
    raise RuntimeError(f"Chưa có API key nào cho {'embedding' if need_embeddings else 'chat'}: cần một trong {keys}")

def _strip_fences(text: str) -> str:
    """Some providers wrap JSON in ```json fences even when asked for raw JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()

def _openai_client(provider: str):
    from openai import OpenAI

    cfg = PROVIDERS[provider]
    return OpenAI(api_key=os.environ[cfg["key"]], base_url=cfg["base_url"])

class MeteredLLM:
    """`chat` and `embed` are drop-in `llm_fn` / `embedding_fn`; `usage` accumulates across calls."""

    def __init__(self, chat_provider: str | None = None, embed_provider: str | None = None) -> None:
        self.chat_provider = chat_provider or pick_provider("LLM_PROVIDER", need_embeddings=False)
        self.embed_provider = embed_provider or pick_provider("EMBEDDING_PROVIDER", need_embeddings=True)
        self.chat_model_id = os.getenv(f"{self.chat_provider.upper()}_CHAT_MODEL", PROVIDERS[self.chat_provider]["chat"])
        self.embed_model_id = os.getenv(f"{self.embed_provider.upper()}_EMBEDDING_MODEL",
                                        PROVIDERS[self.embed_provider]["embed"])
        self.chat_model = f"{self.chat_provider}:{self.chat_model_id}"
        self.embedding_model = f"{self.embed_provider}:{self.embed_model_id}"
        default_max_tokens = "4096" if self.chat_provider == "mwapi" else "16000"
        max_tokens_value = os.getenv(f"{self.chat_provider.upper()}_MAX_TOKENS", default_max_tokens)
        try:
            self.chat_max_tokens = int(max_tokens_value)
        except ValueError as error:
            raise RuntimeError(
                f"{self.chat_provider.upper()}_MAX_TOKENS phải là số nguyên dương"
            ) from error
        if self.chat_max_tokens <= 0:
            raise RuntimeError(f"{self.chat_provider.upper()}_MAX_TOKENS phải là số nguyên dương")
        default_price_multiplier = "5" if self.chat_provider == "mwapi" else "1"
        multiplier_value = os.getenv(
            f"{self.chat_provider.upper()}_PRICE_MULTIPLIER", default_price_multiplier
        )
        try:
            self.chat_price_multiplier = float(multiplier_value)
        except ValueError as error:
            raise RuntimeError(
                f"{self.chat_provider.upper()}_PRICE_MULTIPLIER phải là số dương"
            ) from error
        if self.chat_price_multiplier <= 0:
            raise RuntimeError(f"{self.chat_provider.upper()}_PRICE_MULTIPLIER phải là số dương")
        self._backend_name = self.embedding_model
        self.usage = Usage()
        self._chat_client: Any
        self._embed_client: Any
        if self.chat_provider in ("anthropic", "mwapi"):
            anthropic = importlib.import_module("anthropic")
            cfg = PROVIDERS[self.chat_provider]
            base_url = os.getenv(f"{self.chat_provider.upper()}_BASE_URL", cfg["base_url"] or "").rstrip("/")
            client_options = {"api_key": os.environ[cfg["key"]]}
            if base_url:
                client_options["base_url"] = base_url
            self._chat_client = anthropic.Anthropic(**client_options)
        else:
            self._chat_client = _openai_client(self.chat_provider)
        self._embed_client = (self._chat_client if self.embed_provider == self.chat_provider
                              else _openai_client(self.embed_provider))

    def chat(self, prompt: str, json_mode: bool = False) -> str:
        start = time.perf_counter()
        if self.chat_provider in ("anthropic", "mwapi"):
            text, model, tokens_in, tokens_out = self._chat_anthropic(prompt)
        else:
            if json_mode and self.chat_provider != "gemini":
                response = self._chat_client.chat.completions.create(
                    model=self.chat_model_id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    response_format={"type": "json_object"},
                )
            else:
                response = self._chat_client.chat.completions.create(
                    model=self.chat_model_id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
            text, model = response.choices[0].message.content or "", self.chat_model_id
            usage = response.usage
            tokens_in = usage.prompt_tokens if usage else 0
            tokens_out = usage.completion_tokens if usage else 0
        chat_cost = price(model, tokens_in, tokens_out) * self.chat_price_multiplier
        self.usage += Usage(1, tokens_in, tokens_out, chat_cost, time.perf_counter() - start)
        return _strip_fences(text) if json_mode else text

    def _chat_anthropic(self, prompt: str) -> tuple[str, str, int, int]:
        if self.chat_provider == "mwapi" or "haiku-4-5" in self.chat_model_id:
            # Haiku 4.5 does not accept output_config.effort. Using the stable Messages API also
            # keeps this request compatible with Anthropic-style gateways such as MWAPI.
            response = self._chat_client.messages.create(
                model=self.chat_model_id,
                max_tokens=self.chat_max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "" if response.stop_reason == "refusal" else "".join(
                block.text for block in response.content if block.type == "text"
            )
            return text, response.model, response.usage.input_tokens, response.usage.output_tokens

        # Claude Opus 5.5: thinking is always on and sampling params are removed; effort is the cost lever.
        # Server-side fallback re-runs a policy-declined request on another model inside the same call.
        response = self._chat_client.beta.messages.create(
            model=self.chat_model_id,
            max_tokens=self.chat_max_tokens,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            text = ""
        else:
            text = "".join(block.text for block in response.content if block.type == "text")
        return text, response.model, response.usage.input_tokens, response.usage.output_tokens

    def embed(self, text: str) -> list[float]:
        start = time.perf_counter()
        response = self._embed_client.embeddings.create(model=self.embed_model_id, input=text)
        tokens = getattr(response.usage, "prompt_tokens", 0) or 0   # some OpenAI-compatible APIs omit usage
        self.usage += Usage(1, tokens, 0, price(self.embed_model_id, tokens), time.perf_counter() - start)
        return [float(value) for value in response.data[0].embedding]

    __call__ = embed
