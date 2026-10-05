"""Minimal MWAPI connectivity check (one short Claude response, no Neo4j or embeddings)."""

from __future__ import annotations

import os

from anthropic import Anthropic
from dotenv import load_dotenv


def main() -> int:
    load_dotenv(override=False)
    api_key = os.getenv("MWAPI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("Thiếu MWAPI_API_KEY trong .env")

    base_url = os.getenv("MWAPI_BASE_URL", "https://api.mwapi.dev").rstrip("/")
    model = os.getenv("MWAPI_CHAT_MODEL", "claude-haiku-4-5-20251001")
    client = Anthropic(api_key=api_key, base_url=base_url)
    response = client.messages.create(
        model=model,
        max_tokens=16,
        messages=[{"role": "user", "content": "Chỉ trả lời đúng một từ: OK"}],
    )
    text = "".join(block.text for block in response.content if block.type == "text").strip()
    print(f"[OK] MWAPI kết nối được | model={response.model} | response={text!r}")
    print(f"usage: input={response.usage.input_tokens} output={response.usage.output_tokens}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
