from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DocumentChunk:
    doc_id: str
    chunk_id: str
    title: str
    content: str
    metadata: dict[str, Any]


@dataclass
class RetrievedChunk:
    chunk: DocumentChunk
    score: float
    source: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.chunk.doc_id,
            "chunk_id": self.chunk.chunk_id,
            "title": self.chunk.title,
            "content": self.chunk.content,
            "score": round(self.score, 4),
            "source": self.source,
        }


def _tokenize(text: str) -> list[str]:
    return [word.lower() for word in re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text)]


class KnowledgeAgent:
    """Lightweight, deterministic RAG retrieval agent for business & BI guidelines."""

    def __init__(self, knowledge_dir: Path | None = None) -> None:
        self.knowledge_dir = knowledge_dir
        self.chunks: list[DocumentChunk] = []
        self._doc_frequencies: dict[str, int] = {}
        self._total_chunks: int = 0
        if knowledge_dir and knowledge_dir.exists():
            self.load_directory(knowledge_dir)

    def load_directory(self, directory: Path) -> int:
        count = 0
        for path in sorted(directory.glob("*.md")):
            count += self.load_document(path)
        return count

    def load_document(self, path: Path) -> int:
        text = path.read_text(encoding="utf-8")
        doc_id = path.stem
        # Split markdown by top/second-level headers
        sections = re.split(r"\n(?=#{1,3}\s)", text)
        added = 0
        for idx, section in enumerate(sections):
            section = section.strip()
            if not section:
                continue
            lines = section.splitlines()
            first_line = lines[0].lstrip("#").strip() if lines else doc_id
            chunk = DocumentChunk(
                doc_id=doc_id,
                chunk_id=f"{doc_id}_{idx}",
                title=first_line,
                content=section,
                metadata={"file_path": str(path), "line_count": len(lines)},
            )
            self.chunks.append(chunk)
            added += 1

        self._reindex()
        return added

    def _reindex(self) -> None:
        self._total_chunks = len(self.chunks)
        self._doc_frequencies.clear()
        for chunk in self.chunks:
            tokens = set(_tokenize(chunk.title + " " + chunk.content))
            for token in tokens:
                self._doc_frequencies[token] = self._doc_frequencies.get(token, 0) + 1

    def retrieve(self, query: str, top_k: int = 3) -> list[RetrievedChunk]:
        if not self.chunks or not query:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: list[tuple[DocumentChunk, float]] = []
        for chunk in self.chunks:
            chunk_tokens = _tokenize(chunk.title + " " + chunk.content)
            if not chunk_tokens:
                continue
            
            # Simple TF-IDF / BM25-like scoring
            score = 0.0
            chunk_token_set = set(chunk_tokens)
            for token in query_tokens:
                if token in chunk_token_set:
                    tf = chunk_tokens.count(token) / len(chunk_tokens)
                    df = self._doc_frequencies.get(token, 1)
                    idf = math.log((self._total_chunks + 1) / (df + 0.5)) + 1.0
                    # Give extra weight to matches in the title
                    title_boost = 2.0 if token in _tokenize(chunk.title) else 1.0
                    score += tf * idf * title_boost

            if score > 0.0:
                scores.append((chunk, score))

        scores.sort(key=lambda item: item[1], reverse=True)
        return [
            RetrievedChunk(chunk=chunk, score=score, source=chunk.doc_id)
            for chunk, score in scores[:top_k]
        ]

