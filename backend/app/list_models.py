"""List the Gemini models your API key can actually use.

    python -m app.list_models

Model availability varies by key and changes over time, so if a model name
404s, run this to see valid names for GEMINI_MODEL / GEMINI_EMBED_MODEL.
"""
from __future__ import annotations

from app.llm.gemini_client import _client


def main() -> None:
    embed, generate = [], []
    for m in _client().models.list():
        actions = set(m.supported_actions or [])
        if "embedContent" in actions:
            embed.append(m.name)
        if "generateContent" in actions:
            generate.append(m.name)

    print("Embedding models (for GEMINI_EMBED_MODEL):")
    for n in embed:
        print("  ", n.removeprefix("models/"))
    print("\nGeneration models (for GEMINI_MODEL):")
    for n in generate:
        print("  ", n.removeprefix("models/"))


if __name__ == "__main__":
    main()
