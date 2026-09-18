"""List the Gemini models your API key can actually use.

    python -m app.list_models

Model availability varies by key and changes over time, so if a model name
404s, run this to see valid names for GEMINI_MODEL / GEMINI_EMBED_MODEL.
"""
from __future__ import annotations

import google.generativeai as genai

from app.llm.gemini_client import _ensure_configured


def main() -> None:
    _ensure_configured()
    embed, generate = [], []
    for m in genai.list_models():
        methods = set(m.supported_generation_methods)
        if "embedContent" in methods:
            embed.append(m.name)
        if "generateContent" in methods:
            generate.append(m.name)

    print("Embedding models (for GEMINI_EMBED_MODEL):")
    for n in embed:
        print("  ", n.removeprefix("models/"))
    print("\nGeneration models (for GEMINI_MODEL):")
    for n in generate:
        print("  ", n.removeprefix("models/"))


if __name__ == "__main__":
    main()
