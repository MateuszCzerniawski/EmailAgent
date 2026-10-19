"""
Embedding model abstraction.

Supports two backend strategies selected by model_spec:
  - "all-MiniLM-L6-v2" (or any HuggingFace model name without prefix)
      → sentence_transformers.SentenceTransformer
  - "openai:<model-name>"  e.g. "openai:text-embedding-3-small"
      → openai.OpenAI().embeddings.create()

The model is loaded once at construction time and cached for the lifetime of
the Embedder instance. embed() is synchronous — callers that need async must
wrap it in run_in_executor().
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class Embedder:
    """
    Strategy object that hides the choice of embedding backend.

    Args:
        model_spec: Either a sentence-transformers model name
                    (e.g. "all-MiniLM-L6-v2") or an OpenAI model spec
                    prefixed with "openai:" (e.g. "openai:text-embedding-3-small").

    SentenceTransformer models are cached at class level so the model is
    loaded from disk only once per process, regardless of how many Embedder
    instances are created with the same spec.
    """

    _st_model_cache: dict[str, Any] = {}  # class-level cache: model_spec → model

    def __init__(self, model_spec: str) -> None:
        self._model_spec = model_spec
        self._backend: str  # "sentence_transformers" | "openai"
        self._st_model = None  # SentenceTransformer instance
        self._openai_model: str = ""

        if model_spec.startswith("openai:"):
            self._backend = "openai"
            self._openai_model = model_spec[len("openai:"):]
            import openai  # noqa: F401
            logger.info("Embedder: using OpenAI model '%s'", self._openai_model)
        else:
            self._backend = "sentence_transformers"
            if model_spec not in Embedder._st_model_cache:
                from sentence_transformers import SentenceTransformer
                logger.info("Embedder: loading SentenceTransformer '%s' ...", model_spec)
                Embedder._st_model_cache[model_spec] = SentenceTransformer(model_spec)
                logger.info("Embedder: model loaded and cached")
            else:
                logger.debug("Embedder: reusing cached model '%s'", model_spec)
            self._st_model = Embedder._st_model_cache[model_spec]

    @property
    def model_spec(self) -> str:
        return self._model_spec

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Return a list of embedding vectors (one per input text).

        Args:
            texts: Non-empty list of strings to embed.

        Returns:
            List of float vectors; length matches len(texts).
        """
        if not texts:
            return []

        if self._backend == "sentence_transformers":
            assert self._st_model is not None
            vectors = self._st_model.encode(texts, show_progress_bar=False)
            return [v.tolist() for v in vectors]

        # OpenAI backend
        import openai
        client = openai.OpenAI()
        response = client.embeddings.create(model=self._openai_model, input=texts)
        return [item.embedding for item in response.data]
