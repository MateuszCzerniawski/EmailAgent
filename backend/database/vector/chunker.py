"""
Token-based sliding-window text chunker using tiktoken.

Uses cl100k_base encoding (GPT-4 / text-embedding-3 tokenizer) as a proxy
for counting tokens. This is consistent regardless of whether the embedding
model is sentence-transformers or OpenAI.
"""

from __future__ import annotations

import tiktoken


class TokenChunker:
    """
    Split text into overlapping token-window chunks.

    Args:
        chunk_size:   Maximum number of tokens per chunk.
        chunk_overlap: Number of tokens shared between consecutive chunks.
        encoding_name: tiktoken encoding to use for tokenisation.
    """

    def __init__(
        self,
        chunk_size: int = 400,
        chunk_overlap: int = 80,
        encoding_name: str = "cl100k_base",
    ) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._enc = tiktoken.get_encoding(encoding_name)

    def chunk(self, text: str) -> list[str]:
        """
        Return a list of text chunks, each at most chunk_size tokens,
        with chunk_overlap tokens of context from the previous chunk.

        Returns an empty list if text is empty or tokenises to zero tokens.
        """
        if not text or not text.strip():
            return []

        tokens = self._enc.encode(text)
        if not tokens:
            return []

        step = self._chunk_size - self._chunk_overlap
        if step <= 0:
            step = 1

        chunks: list[str] = []
        start = 0
        while start < len(tokens):
            end = min(start + self._chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunks.append(self._enc.decode(chunk_tokens))
            if end == len(tokens):
                break
            start += step

        return chunks
